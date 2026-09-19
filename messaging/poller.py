"""
RecoveryLens — messaging/poller.py

Fetch inbound WhatsApp replies from Twilio instead of waiting to be called.

WHY THIS EXISTS
---------------
The webhook is the right design: Twilio POSTs the moment a carer replies, and
the reply is handled in under a second. But it depends on a URL configured in
Twilio's console, and that console has moved the setting twice — the WhatsApp
Sandbox configuration now lives only in a "legacy Console" that upgraded
accounts can no longer reach. A deployment whose inbound path can be broken by
someone else's navigation redesign is a deployment with a single point of
failure outside its own repository.

So this is the fallback: ask Twilio what has arrived, rather than being told.
It needs no webhook, no public URL, no signature validation and no console
setting, which means it works identically on a laptop behind NAT and on a
deployed host.

IT IS NOT A REPLACEMENT
-----------------------
Polling is worse in every way that matters in production: it is slower by up to
one interval, it burns API calls on quiet accounts, and two processes polling
the same account will both try to handle the same message. The webhook stays the
primary path. This is what you turn on when the webhook cannot be configured,
and what you turn off again when it can.

THE SAFETY PROPERTIES ARE NOT DUPLICATED HERE
---------------------------------------------
Everything that decides what happens to a reply — opt-out first, no recording of
speech without confirmation, rules before agent, agent may only escalate — lives
in `api.webhooks.process_inbound` and is called from here. This module only
answers "which messages are new", which is the one question a webhook answers
for free and polling has to work out for itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os

# How far back to look on each poll. Generous relative to the interval so a
# slow cycle, a restart, or a few seconds of clock skew cannot step over a
# message. Re-seeing a message is free — ProcessedInbound makes it a no-op.
LOOKBACK = timedelta(minutes=30)

# Twilio returns newest-first. A carer sends one message per check-in, so this
# is only ever hit when something is wrong, and truncating is better than
# paging through an account's entire history on a misconfiguration.
MAX_PER_POLL = 50


@dataclass
class PollResult:
    checked: int = 0
    handled: int = 0
    skipped: int = 0
    replies_sent: int = 0
    errors: list[str] = field(default_factory=list)
    outcomes: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "checked": self.checked, "handled": self.handled,
            "skipped": self.skipped, "replies_sent": self.replies_sent,
            "errors": self.errors, "outcomes": self.outcomes,
        }


def polling_enabled() -> bool:
    """Off unless asked for. See the module docstring — this is the fallback."""
    return os.getenv("RECOVERYLENS_INBOUND_POLL", "").strip().lower() in {
        "1", "true", "yes", "on"}


def poll_interval_seconds() -> int:
    raw = os.getenv("RECOVERYLENS_INBOUND_POLL_SECONDS", "").strip()
    try:
        return max(5, int(raw)) if raw else 10
    except ValueError:
        return 10


def _client():
    """A Twilio REST client, or None with a stated reason."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not (sid and token):
        return None, "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not both set"
    try:
        from twilio.rest import Client
    except ImportError:
        return None, "the twilio SDK is not installed"
    return Client(sid, token), ""


def poll_once(session_factory) -> PollResult:
    """One pass. Never raises — a failed poll must not stop the next one."""
    from api.database import ProcessedInbound
    from api.webhooks import process_inbound

    from .sender import build_sender

    result = PollResult()

    client, why = _client()
    if client is None:
        result.errors.append(why)
        return result

    inbox = os.getenv("TWILIO_FROM", "").strip()
    if not inbox:
        result.errors.append("TWILIO_FROM is not set, so there is no number to poll")
        return result

    try:
        # `to=` is our own number: these are messages sent TO us. Without it the
        # list also returns everything we sent, and an outbound message
        # processed as a reply would complete the check-in it was announcing.
        messages = client.messages.list(
            to=inbox,
            date_sent_after=datetime.now(timezone.utc) - LOOKBACK,
            limit=MAX_PER_POLL,
        )
    except Exception as exc:
        result.errors.append(f"{type(exc).__name__}: {exc}"[:200])
        return result

    sender = build_sender()

    # Oldest first. A carer who sends "no" and then "she had a fall" should have
    # those applied in the order they were written.
    for msg in sorted(messages, key=lambda m: m.date_sent or datetime.min):
        result.checked += 1

        if getattr(msg, "direction", "") not in ("inbound", ""):
            result.skipped += 1
            continue

        session = session_factory()
        try:
            if session.get(ProcessedInbound, msg.sid):
                result.skipped += 1
                continue

            media_url = None
            media_type = "audio/ogg"
            if int(getattr(msg, "num_media", 0) or 0) > 0:
                try:
                    media = client.messages(msg.sid).media.list(limit=1)
                    if media:
                        media_type = media[0].content_type or media_type
                        # The authenticated media URL. `_handle_voice_note`
                        # fetches it with the same credentials.
                        media_url = (f"https://api.twilio.com{media[0].uri}"
                                     .replace(".json", ""))
                except Exception as exc:
                    result.errors.append(f"media for {msg.sid}: {type(exc).__name__}")

            outcome = process_inbound(
                session,
                from_number=msg.from_ or "",
                text=(msg.body or "").strip(),
                media_url=media_url,
                media_type=media_type,
            )

            # Claim the message BEFORE sending the reply. A crash between the
            # two costs the carer a confirmation; the other order would re-run
            # triage on the next poll and could re-escalate a closed check-in.
            session.add(ProcessedInbound(sid=msg.sid, outcome=outcome.outcome))
            session.commit()
            result.handled += 1
            result.outcomes.append({"sid": msg.sid, "outcome": outcome.outcome})

            if outcome.reply:
                sent = sender.send(msg.from_ or "", outcome.reply)
                if sent.ok:
                    result.replies_sent += 1
                else:
                    result.errors.append(f"reply to {msg.sid}: {sent.error}")

            # The agent runs inline here. There is no request timeout to beat —
            # the webhook defers it only because Twilio hangs up after ~15s.
            if outcome.should_run_agent:
                try:
                    from api.webhooks import _run_triage_agent

                    _run_triage_agent(outcome.checkin_id, outcome.agent_free_text,
                                      list(outcome.agent_rule_reasons))
                except Exception as exc:
                    result.errors.append(
                        f"agent for {msg.sid}: {type(exc).__name__}: {exc}"[:160])

        except Exception as exc:
            session.rollback()
            result.errors.append(f"{msg.sid}: {type(exc).__name__}: {exc}"[:200])
        finally:
            session.close()

    return result


def start(session_factory):
    """Background polling, if enabled. Returns the scheduler or None.

    Separate from the outbound scheduler on purpose: that one SENDS messages to
    families on a timer and is off by default for good reason. Reading replies
    carries none of that risk, so it is a different switch.
    """
    if not polling_enabled():
        print("[inbound-poll] off (set RECOVERYLENS_INBOUND_POLL=1 to read "
              "replies without a webhook)")
        return None

    from apscheduler.schedulers.background import BackgroundScheduler

    seconds = poll_interval_seconds()
    scheduler = BackgroundScheduler(timezone="UTC")

    def job():
        outcome = poll_once(session_factory)
        if outcome.handled or outcome.errors:
            print(f"[inbound-poll] checked={outcome.checked} "
                  f"handled={outcome.handled} replies={outcome.replies_sent} "
                  f"errors={outcome.errors}")

    # `max_instances=1` and coalescing: a slow poll must not overlap the next
    # one, and a backlog after a pause should collapse to a single run rather
    # than firing a dozen times in a row.
    scheduler.add_job(job, "interval", seconds=seconds,
                      max_instances=1, coalesce=True, id="inbound-poll")
    scheduler.start()
    print(f"[inbound-poll] ON — reading replies from Twilio every {seconds}s")
    return scheduler
