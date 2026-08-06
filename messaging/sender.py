"""
RecoveryLens — messaging/sender.py
==================================
Outbound message delivery, behind a protocol.

Why a protocol rather than calling Twilio directly
--------------------------------------------------
Everything that matters here — consent, opt-out, rate limiting, whether a
message should be sent at all — is policy, not transport. Putting Twilio behind
an interface means all of that is testable without credentials, without network,
and without the risk of a test suite accidentally messaging a real phone.

ConsoleSender is the default. You have to configure Twilio deliberately; there
is no path where a missing environment variable results in real messages going
somewhere unexpected.

The 24-hour window
------------------
WhatsApp only permits free-form messages within 24 hours of the user's last
message. Outside that window you may only send pre-approved templates. This
matters directly: a check-in scheduled for day 42 is far outside any session, so
in production those sends need approved templates. The sandbox does not enforce
it, which makes it exactly the kind of thing that works in a demo and fails in
deployment. `SessionWindow` records the constraint rather than pretending it
does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
import os
import re

# WhatsApp's customer-service window. Outside it, only templates are allowed.
SESSION_WINDOW = timedelta(hours=24)

# Words that stop all messaging, permanently. Matched case-insensitively as a
# whole word anywhere in the reply — someone typing "please STOP these" means it.
STOP_WORDS = {"stop", "unsubscribe", "cancel", "quit", "end", "optout", "opt-out"}

_STOP_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in STOP_WORDS) + r")\b",
                      re.IGNORECASE)


def is_stop_request(text: str) -> bool:
    """Whether a reply is an opt-out.

    Deliberately generous. A false positive means someone stops receiving
    check-ins they wanted and has to be re-enrolled by a human — annoying. A
    false negative means continuing to message someone who asked you to stop,
    which is a regulatory problem and, more to the point, wrong.
    """
    return bool(_STOP_RE.search(text or ""))


@dataclass
class SendResult:
    ok: bool
    channel: str
    to: str
    message_id: str | None = None
    error: str | None = None
    suppressed_reason: str | None = None   # set when policy blocked the send


class Sender(Protocol):
    channel: str

    def send(self, to: str, body: str) -> SendResult: ...


class ConsoleSender:
    """Prints instead of sending. The default, deliberately.

    Used in development, in tests, and any time Twilio is not configured. A
    check-in that would have gone out appears in the server log, so the flow is
    fully exercisable with no account and no risk of contacting anyone.
    """

    channel = "console"

    def __init__(self, echo: bool = True):
        self.echo = echo
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, body: str) -> SendResult:
        self.sent.append((to, body))
        if self.echo:
            print(f"\n--- [console sender] to {to} ---\n{body}\n---\n")
        return SendResult(ok=True, channel=self.channel, to=to,
                          message_id=f"console-{len(self.sent)}")


class TwilioSender:
    """WhatsApp or SMS via Twilio.

    Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM. `TWILIO_FROM`
    should carry the `whatsapp:` prefix for WhatsApp — Twilio treats
    `whatsapp:+14155238886` and `+14155238886` as different channels entirely,
    and mismatching them is the commonest first-time failure.
    """

    channel = "twilio"

    def __init__(self, account_sid: str | None = None, auth_token: str | None = None,
                 from_: str | None = None):
        self.account_sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token = auth_token or os.getenv("TWILIO_AUTH_TOKEN", "")
        self.from_ = from_ or os.getenv("TWILIO_FROM", "")
        if not all([self.account_sid, self.auth_token, self.from_]):
            raise RuntimeError(
                "TwilioSender needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and "
                "TWILIO_FROM. See .env.example.")

    def send(self, to: str, body: str) -> SendResult:
        try:
            from twilio.rest import Client
        except ImportError:
            return SendResult(ok=False, channel=self.channel, to=to,
                              error="twilio SDK not installed")
        try:
            client = Client(self.account_sid, self.auth_token)
            msg = client.messages.create(
                body=body, from_=self.from_, to=_normalise(to, self.from_))
            return SendResult(ok=True, channel=self.channel, to=to,
                              message_id=msg.sid)
        except Exception as exc:
            # A failed send must never lose the check-in. The caller leaves it
            # pending and retries; see scheduler.py.
            return SendResult(ok=False, channel=self.channel, to=to,
                              error=_readable_twilio_error(exc))


# Twilio's exception text is a formatted terminal block: ANSI colour codes, the
# request echoed back, then the actual reason. Truncating it blind spent the
# whole budget on escape sequences and cut off the only useful part.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The failures worth naming, because the raw message does not say what to do.
TWILIO_HINTS = {
    "63016": ("Outside WhatsApp's 24-hour window. Free-form messages are only "
              "allowed within 24h of the recipient's last message. Send anything "
              "to the sandbox number from your phone, then retry."),
    "63007": ("Twilio has no WhatsApp channel for that From address. Check "
              "TWILIO_FROM matches the sandbox number and starts with 'whatsapp:'."),
    "63015": ("The recipient has not joined the sandbox. Send the 'join <code>' "
              "phrase from that phone first."),
    "21910": ("From and To are on different channels — one has the 'whatsapp:' "
              "prefix and the other does not."),
    # 20003 is overloaded. Twilio returns it for genuine auth failures AND for
    # regulatory refusals, and the two need completely different responses.
    # An earlier version of this table said only "check your credentials", which
    # sent a real compliance block down entirely the wrong path.
    "20003": ("Authentication OR compliance. Read the message text: if it "
              "mentions a 'compliance profile' or 'Trust Hub', your credentials "
              "are fine and Twilio is refusing on regulatory grounds — paid "
              "accounts need KYC before messaging certain destinations, which "
              "takes business documents and days of review, not a code change. "
              "Only if it says authentication failed should you check "
              "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."),
    "21608": ("Unverified number on a trial account. Verify it in the Twilio "
              "console, or use the WhatsApp sandbox."),
    "572002": ("Trial account: the destination must be a Verified Caller ID. "
               "Twilio Console -> Phone Numbers -> Manage -> Verified Caller IDs "
               "-> Add a new Caller ID, then enter the code Twilio sends. This "
               "applies even to the WhatsApp sandbox, and catches most people "
               "out on international numbers."),
    "63003": ("Twilio cannot reach that WhatsApp recipient. Usually the number "
              "has no WhatsApp account, or has not joined the sandbox."),
    "21654": ("The 24-hour window, in its modern form. WhatsApp requires a "
              "pre-approved template (ContentSid) for business-initiated "
              "messages; free-form is only allowed within 24h of the recipient "
              "messaging you. Send anything to the sandbox number from that "
              "phone, then retry immediately. THIS IS THE CONSTRAINT THAT BITES "
              "IN PRODUCTION: a day-42 check-in is always outside the window, so "
              "real deployment needs Meta-approved templates."),
}


def _readable_twilio_error(exc: Exception) -> str:
    """Turn Twilio's terminal-formatted exception into one useful line."""
    raw = _ANSI.sub("", str(exc))
    raw = " ".join(raw.split())

    code = getattr(exc, "code", None)
    if code is None:
        m = re.search(r"\b(2\d{4}|6\d{4})\b", raw)
        code = m.group(1) if m else None

    message = getattr(exc, "msg", "") or raw
    parts = [f"{type(exc).__name__}"]
    if code:
        parts.append(f"[{code}]")
    parts.append(str(message)[:300])

    hint = TWILIO_HINTS.get(str(code))
    if hint:
        parts.append(f"— {hint}")
    return " ".join(parts)


def _normalise(to: str, from_: str) -> str:
    """Match the recipient's channel prefix to the sender's.

    Twilio silently treats a WhatsApp `from` with a bare `to` as an SMS attempt
    and rejects it with an unhelpful error. Cheap to prevent here.
    """
    to = (to or "").strip()
    if from_.startswith("whatsapp:") and not to.startswith("whatsapp:"):
        return f"whatsapp:{to}"
    if not from_.startswith("whatsapp:") and to.startswith("whatsapp:"):
        return to.replace("whatsapp:", "", 1)
    return to


def build_sender() -> Sender:
    """Twilio only when explicitly configured; console otherwise."""
    if os.getenv("RECOVERYLENS_MESSAGING", "").strip().lower() == "twilio":
        try:
            return TwilioSender()
        except RuntimeError as exc:
            print(f"[messaging] Twilio requested but not configured ({exc}). "
                  f"Falling back to console.")
    return ConsoleSender()


def within_session_window(last_inbound: datetime | None,
                          now: datetime | None = None) -> bool:
    """Whether a free-form WhatsApp message is permitted right now."""
    if last_inbound is None:
        return False
    now = now or datetime.now(timezone.utc)
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)
    return (now - last_inbound) < SESSION_WINDOW
