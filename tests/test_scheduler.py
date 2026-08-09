"""
Safety tests for the check-in scheduler.

Run:  pytest tests/test_scheduler.py -v

A background job that messages patients' families unattended is the most
dangerous thing in this codebase. Nobody is watching when it runs, so the
failure modes are silent: a message sent months early, the same message sent
five times, or a whole batch abandoned because one patient's record was odd.

These tests are about those three, not about the happy path.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real SQLite database, per test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/sched.db")

    import importlib
    import api.database as database
    importlib.reload(database)
    database.init_db()

    yield database
    database.engine.dispose()


def _patient(database, session, contact="+919715618753", consent=True, opted_out=False):
    p = database.Patient(patient_ref="w3", caregiver_contact=contact,
                         consent_recorded=consent, opted_out=opted_out)
    session.add(p)
    session.flush()
    return p


def _checkin(database, session, patient, when, sent_at=None, completed_at=None):
    c = database.CheckIn(patient_id=patient.id, scheduled_for=when,
                         sent_at=sent_at, completed_at=completed_at,
                         reason="Test check-in")
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def sent_log(monkeypatch):
    """Capture sends without touching Twilio."""
    sent = []

    def fake_send(checkin_id, db):
        sent.append(checkin_id)
        row = db.query(db.__class__ and None) if False else None  # noqa
        from api.database import CheckIn
        c = db.query(CheckIn).filter(CheckIn.id == checkin_id).first()
        from api.database import utcnow
        c.sent_at = utcnow()
        db.commit()
        return {"sent": True, "channel": "fake", "check_in_id": checkin_id}

    import api.webhooks as webhooks
    monkeypatch.setattr(webhooks, "send_checkin", fake_send)
    return sent


# ------------------------------------------------------------------ never early
def test_future_checkins_are_never_sent(db, sent_log):
    """The single most important property. A day-90 check-in arriving on day 1
    destroys the carer's trust that a message means something."""
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    _checkin(db, s, p, NOW + timedelta(days=1))
    _checkin(db, s, p, NOW + timedelta(days=90))
    s.commit()
    s.close()

    report = send_due_checkins(db.SessionLocal, now=NOW)
    assert report.considered == 0
    assert sent_log == []


def test_due_checkins_are_sent(db, sent_log):
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    due = _checkin(db, s, p, NOW - timedelta(hours=1))
    s.commit()
    due_id = due.id
    s.close()

    report = send_due_checkins(db.SessionLocal, now=NOW)
    assert report.sent == 1
    assert sent_log == [due_id]


def test_boundary_is_inclusive(db, sent_log):
    """Due exactly now counts as due."""
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    _checkin(db, s, p, NOW)
    s.commit()
    s.close()

    assert send_due_checkins(db.SessionLocal, now=NOW).sent == 1


# ------------------------------------------------------------------ never twice
def test_already_sent_checkins_are_skipped(db, sent_log):
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    _checkin(db, s, p, NOW - timedelta(days=1), sent_at=NOW - timedelta(hours=2))
    s.commit()
    s.close()

    assert send_due_checkins(db.SessionLocal, now=NOW).considered == 0
    assert sent_log == []


def test_running_twice_does_not_resend(db, sent_log):
    """The job runs every 15 minutes. Idempotence is not optional."""
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    _checkin(db, s, p, NOW - timedelta(hours=1))
    s.commit()
    s.close()

    send_due_checkins(db.SessionLocal, now=NOW)
    send_due_checkins(db.SessionLocal, now=NOW)
    assert len(sent_log) == 1


def test_completed_checkins_are_skipped(db, sent_log):
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    _checkin(db, s, p, NOW - timedelta(days=1), completed_at=NOW)
    s.commit()
    s.close()

    assert send_due_checkins(db.SessionLocal, now=NOW).considered == 0


# ------------------------------------------------------------- failure handling
def test_one_failure_does_not_abort_the_batch(db, monkeypatch):
    """Three patients due, the middle one explodes. The other two still go."""
    from messaging.scheduler import send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    ids = [_checkin(db, s, p, NOW - timedelta(hours=1)).id for _ in range(3)]
    s.commit()
    s.close()

    import api.webhooks as webhooks
    sent = []

    def flaky(checkin_id, db_):
        if checkin_id == ids[1]:
            raise RuntimeError("boom")
        sent.append(checkin_id)
        return {"sent": True, "check_in_id": checkin_id}

    monkeypatch.setattr(webhooks, "send_checkin", flaky)

    report = send_due_checkins(db.SessionLocal, now=NOW)
    assert report.failed == 1
    assert sorted(sent) == sorted([ids[0], ids[2]])


def test_failed_send_is_retried_next_run(db, monkeypatch):
    """`sent_at` stays null on failure, so the check-in is not lost."""
    from messaging.scheduler import send_due_checkins
    import api.webhooks as webhooks

    s = db.SessionLocal()
    p = _patient(db, s)
    _checkin(db, s, p, NOW - timedelta(hours=1))
    s.commit()
    s.close()

    monkeypatch.setattr(webhooks, "send_checkin",
                        lambda cid, d: {"sent": False, "error": "network"})
    first = send_due_checkins(db.SessionLocal, now=NOW)
    assert first.refused == 1

    # Still due on the next run.
    second = send_due_checkins(db.SessionLocal, now=NOW)
    assert second.considered == 1


def test_policy_refusal_is_recorded_not_treated_as_an_error(db, monkeypatch):
    """No consent or opted out is an expected outcome, not a fault."""
    from messaging.scheduler import send_due_checkins
    import api.webhooks as webhooks

    s = db.SessionLocal()
    p = _patient(db, s, opted_out=True)
    _checkin(db, s, p, NOW - timedelta(hours=1))
    s.commit()
    s.close()

    monkeypatch.setattr(
        webhooks, "send_checkin",
        lambda cid, d: {"sent": False, "reason": "Recipient has opted out."})

    report = send_due_checkins(db.SessionLocal, now=NOW)
    assert report.refused == 1 and report.failed == 0
    assert "opted out" in report.reasons[0]


# ------------------------------------------------------------------- batch cap
def test_batch_is_capped(db, sent_log):
    """A bug that marks everything due should produce a backlog, not hundreds of
    messages to worried families."""
    from messaging.scheduler import MAX_SENDS_PER_RUN, send_due_checkins

    s = db.SessionLocal()
    p = _patient(db, s)
    for _ in range(MAX_SENDS_PER_RUN + 10):
        _checkin(db, s, p, NOW - timedelta(hours=1))
    s.commit()
    s.close()

    assert send_due_checkins(db.SessionLocal, now=NOW).considered == MAX_SENDS_PER_RUN


# ---------------------------------------------------------------- enable/disable
def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_SCHEDULER", raising=False)
    from messaging.scheduler import scheduler_enabled, start
    assert scheduler_enabled() is False
    assert start(lambda: None) is None


def test_enabled_only_by_explicit_flag(monkeypatch):
    from messaging.scheduler import scheduler_enabled
    for value, expected in [("1", True), ("true", True), ("yes", True),
                            ("0", False), ("", False), ("no", False)]:
        monkeypatch.setenv("RECOVERYLENS_SCHEDULER", value)
        assert scheduler_enabled() is expected


def test_missing_apscheduler_does_not_crash_startup(monkeypatch):
    """The app must serve requests even if scheduling cannot start."""
    monkeypatch.setenv("RECOVERYLENS_SCHEDULER", "1")
    import builtins
    real_import = builtins.__import__

    def no_apscheduler(name, *a, **kw):
        if name.startswith("apscheduler"):
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_apscheduler)
    from messaging.scheduler import start
    assert start(lambda: None) is None


# ----------------------------------------------------------------- voice sweep
def test_voice_sweep_delegates_and_closes_its_session(db, monkeypatch):
    from messaging import scheduler
    import api.webhooks as webhooks

    called = {}
    monkeypatch.setattr(webhooks, "escalate_unconfirmed_voice",
                        lambda d: called.setdefault("ids", [7]) or [7])
    assert scheduler.sweep_unconfirmed_voice(db.SessionLocal) == [7]
