"""
RecoveryLens — messaging/scheduler.py
=====================================
Sends scheduled check-ins without anyone pressing a button.

Until this existed, `POST /api/checkins/{id}/send` worked and nothing called it.
The product claimed to follow patients up; in practice a human had to remember.

What it does
------------
Two jobs, both idempotent, both safe to run repeatedly:

  send_due_checkins()          every SEND_INTERVAL_MINUTES
      Finds check-ins whose date has arrived and which have not been sent, and
      sends each through the same policy-gated path the API uses.

  sweep_unconfirmed_voice()    every SWEEP_INTERVAL_MINUTES
      Escalates voice transcripts a carer never confirmed, so a recording we
      could not verify reaches a clinician instead of sitting unread.

The rules it must not break
---------------------------
1. NEVER SEND EARLY. It queries `scheduled_for <= now` only. The API has an
   `include_scheduled` flag for demoing the carer screen; the scheduler must not
   use it. Sending a day-90 check-in on day 1 is worse than sending nothing —
   it destroys the carer's trust that a message means something.

2. NEVER SEND TWICE. `sent_at` is set on success and filtered on. The 12-hour
   rate limit in policy.py is a second line of defence, not the first.

3. A FAILURE MUST NOT LOSE THE CHECK-IN. `sent_at` stays null when a send fails,
   so the next run retries it. One patient's failure never aborts the batch.

4. OFF BY DEFAULT. Set RECOVERYLENS_SCHEDULER=1 deliberately. A background job
   that messages patients' families should never start because someone forgot to
   turn it off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import traceback

# How often to look for due check-ins. Check-ins are days apart, so this is
# generous — it exists so a check-in due at 09:00 does not wait until tomorrow,
# not to be responsive.
SEND_INTERVAL_MINUTES = 15
SWEEP_INTERVAL_MINUTES = 30

# Cap per run. A bug that marks hundreds of check-ins due should produce a
# visible backlog, not hundreds of WhatsApp messages to worried families.
MAX_SENDS_PER_RUN = 25


def scheduler_enabled() -> bool:
    return os.getenv("RECOVERYLENS_SCHEDULER", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class RunReport:
    considered: int = 0
    sent: int = 0
    refused: int = 0
    failed: int = 0
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.sent} sent, {self.refused} refused by policy, "
                f"{self.failed} failed, of {self.considered} due")


def send_due_checkins(db_factory, now=None) -> RunReport:
    """Send every check-in whose date has arrived.

    `db_factory` is a callable returning a session — injected so this is testable
    without a running app, and so the job owns its session rather than borrowing
    a request-scoped one.
    """
    from datetime import datetime, timezone

    from api.database import CheckIn
    from api.webhooks import send_checkin

    now = now or datetime.now(timezone.utc)
    report = RunReport()
    db = db_factory()

    try:
        due = (db.query(CheckIn)
               .filter(CheckIn.completed_at.is_(None),
                       CheckIn.sent_at.is_(None),
                       CheckIn.scheduled_for <= now)
               .order_by(CheckIn.scheduled_for)
               .limit(MAX_SENDS_PER_RUN)
               .all())
        report.considered = len(due)

        for checkin in due:
            try:
                result = send_checkin(checkin.id, db)
                if result.get("sent"):
                    report.sent += 1
                else:
                    # Refused by the policy gate — no consent, opted out, rate
                    # limited. Expected, not an error; `sent_at` stays null so it
                    # is reconsidered next run once the reason clears.
                    report.refused += 1
                    reason = result.get("reason") or result.get("error") or "unknown"
                    report.reasons.append(f"#{checkin.id}: {reason}")
            except Exception as exc:
                # One patient's failure must not abort the batch.
                report.failed += 1
                report.reasons.append(f"#{checkin.id}: {type(exc).__name__}: {exc}")
                print(f"[scheduler] send failed for check-in {checkin.id}")
                traceback.print_exc()
    finally:
        db.close()

    return report


def sweep_unconfirmed_voice(db_factory) -> list[int]:
    """Escalate voice transcripts nobody confirmed. See voice/confirm.py."""
    from api.webhooks import escalate_unconfirmed_voice

    db = db_factory()
    try:
        return escalate_unconfirmed_voice(db)
    finally:
        db.close()


def start(db_factory) -> object | None:
    """Start the background scheduler. Returns it, or None if disabled.

    Called from the API's startup hook. Any failure here is logged and swallowed:
    the app must serve requests even if the scheduler cannot start.
    """
    if not scheduler_enabled():
        print("[scheduler] disabled. Check-ins are sent manually via "
              "POST /api/checkins/{id}/send. Set RECOVERYLENS_SCHEDULER=1 to "
              "enable.")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[scheduler] apscheduler not installed; scheduling is off. "
              "pip install apscheduler==3.11.0")
        return None

    scheduler = BackgroundScheduler(timezone="UTC")

    def _send_job():
        report = send_due_checkins(db_factory)
        if report.considered:
            print(f"[scheduler] {report.summary()}")
            for reason in report.reasons[:5]:
                print(f"[scheduler]   {reason}")

    def _sweep_job():
        ids = sweep_unconfirmed_voice(db_factory)
        if ids:
            print(f"[scheduler] escalated {len(ids)} unconfirmed voice "
                  f"transcripts: {ids}")

    # coalesce: if the app was asleep, run once on wake rather than once per
    # missed interval — the Render free tier sleeps after 15 minutes idle, and
    # a night's worth of catch-up runs firing at once would be a bad surprise.
    scheduler.add_job(_send_job, "interval", minutes=SEND_INTERVAL_MINUTES,
                      id="send_due_checkins", coalesce=True, max_instances=1)
    scheduler.add_job(_sweep_job, "interval", minutes=SWEEP_INTERVAL_MINUTES,
                      id="sweep_unconfirmed_voice", coalesce=True, max_instances=1)

    scheduler.start()
    print(f"[scheduler] running. Sends every {SEND_INTERVAL_MINUTES}m, "
          f"voice sweep every {SWEEP_INTERVAL_MINUTES}m, "
          f"max {MAX_SENDS_PER_RUN} sends per run.")
    return scheduler
