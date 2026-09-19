"""
Tests for reading carer replies by polling instead of by webhook.

WHY POLLING NEEDS ITS OWN TESTS
-------------------------------
The webhook is called once per message. Polling sees the same recent messages
on every cycle, so the question a webhook never has to answer — "have I already
acted on this?" — becomes the whole correctness problem. Get it wrong and a
single "yes" from a carer re-completes the check-in, re-runs the agent and
re-sends a confirmation every ten seconds.

The clinical safety properties are NOT retested here. They live in
`api.webhooks.process_inbound`, which both routes call, and are covered by
tests/test_messaging.py. That shared function is the point: two copies would
eventually disagree, and the disagreement would be in whichever half was harder
to test.
"""

from datetime import datetime, timezone
import types

import pytest

from messaging.poller import poll_once, poll_interval_seconds, polling_enabled


class _Msg:
    """Just enough of a Twilio message for the poller."""

    def __init__(self, sid, from_, body, direction="inbound", num_media=0):
        self.sid = sid
        self.from_ = from_
        self.body = body
        self.direction = direction
        self.num_media = num_media
        self.date_sent = datetime.now(timezone.utc)


def _fake_twilio(messages):
    """A client whose `messages.list` returns exactly what we give it."""
    client = types.SimpleNamespace()
    client.messages = types.SimpleNamespace(list=lambda **kw: list(messages))
    return client


@pytest.fixture
def twilio_env(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "faketoken")
    monkeypatch.setenv("TWILIO_FROM", "whatsapp:+14155238886")
    # Console sender: replies are printed, not sent to a real phone.
    monkeypatch.delenv("RECOVERYLENS_MESSAGING", raising=False)


def _patient(db, org_id, contact="+919876543210"):
    from api.database import CheckIn, Patient, utcnow

    p = Patient(organisation_id=org_id, patient_ref="WARD-POLL",
                caregiver_contact=contact, consent_recorded=True)
    db.add(p)
    db.commit()
    c = CheckIn(patient_id=p.id, scheduled_for=utcnow(), reason="Day 3",
                sent_at=utcnow())
    db.add(c)
    db.commit()
    return p, c


def test_a_reply_completes_the_checkin(monkeypatch, db, org_id, twilio_env):
    from api.database import CheckIn, SessionLocal

    _, checkin = _patient(db, org_id)
    monkeypatch.setattr("messaging.poller._client",
                        lambda: (_fake_twilio([
                            _Msg("SM1", "whatsapp:+919876543210", "no yes yes")]), ""))

    result = poll_once(SessionLocal)

    assert result.handled == 1
    db.expire_all()
    fresh = db.get(CheckIn, checkin.id)
    assert fresh.completed_at is not None, "the reply did not complete the check-in"
    assert fresh.escalated is True, "'no yes yes' should escalate on the rules alone"


def test_the_same_message_is_never_processed_twice(monkeypatch, db, org_id, twilio_env):
    """The whole reason ProcessedInbound exists.

    Twilio returns the same recent messages on every poll. Without the dedup
    record, a carer's single reply would be re-triaged every cycle — and the
    confirmation re-sent to their phone every ten seconds.
    """
    from api.database import SessionLocal

    _patient(db, org_id)
    message = _Msg("SM-DUPE", "whatsapp:+919876543210", "yes yes no")
    monkeypatch.setattr("messaging.poller._client",
                        lambda: (_fake_twilio([message]), ""))

    first = poll_once(SessionLocal)
    second = poll_once(SessionLocal)
    third = poll_once(SessionLocal)

    assert first.handled == 1
    assert second.handled == 0 and second.skipped == 1
    assert third.handled == 0 and third.skipped == 1
    assert second.replies_sent == 0, "a duplicate poll re-sent the confirmation"


def test_outbound_messages_are_never_treated_as_replies(monkeypatch, db, org_id,
                                                        twilio_env):
    """A message we sent, processed as a reply, would complete the very
    check-in it was announcing. The `to=` filter is the primary guard; this
    pins the direction check behind it."""
    from api.database import CheckIn, SessionLocal

    _, checkin = _patient(db, org_id)
    monkeypatch.setattr("messaging.poller._client",
                        lambda: (_fake_twilio([
                            _Msg("SM-OUT", "whatsapp:+14155238886",
                                 "RecoveryLens check-in for WARD-POLL",
                                 direction="outbound-api")]), ""))

    result = poll_once(SessionLocal)

    assert result.handled == 0
    db.expire_all()
    assert db.get(CheckIn, checkin.id).completed_at is None


def test_a_stop_reply_opts_out_even_while_polling(monkeypatch, db, org_id, twilio_env):
    """Opt-out must not depend on which route the message arrived by."""
    from api.database import Patient, SessionLocal

    patient, _ = _patient(db, org_id)
    monkeypatch.setattr("messaging.poller._client",
                        lambda: (_fake_twilio([
                            _Msg("SM-STOP", "whatsapp:+919876543210", "STOP")]), ""))

    poll_once(SessionLocal)

    db.expire_all()
    assert db.get(Patient, patient.id).opted_out is True


def test_missing_credentials_report_a_reason_rather_than_crashing(monkeypatch, db):
    from api.database import SessionLocal

    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    result = poll_once(SessionLocal)

    assert result.handled == 0
    assert result.errors, "a misconfigured poll must say so, not look like 'no replies'"


def test_a_twilio_failure_is_reported_not_raised(monkeypatch, db, twilio_env):
    """A failed poll must never stop the next one — the job runs on a timer."""
    from api.database import SessionLocal

    def exploding():
        client = types.SimpleNamespace()

        def boom(**kw):
            raise RuntimeError("Twilio is having a day")

        client.messages = types.SimpleNamespace(list=boom)
        return client, ""

    monkeypatch.setattr("messaging.poller._client", exploding)

    result = poll_once(SessionLocal)      # must not raise

    assert result.handled == 0
    assert any("Twilio is having a day" in e for e in result.errors)


def test_polling_is_off_unless_asked_for(monkeypatch):
    """The webhook is the primary path. This is the fallback, and a fallback
    that switches itself on is not a fallback."""
    monkeypatch.delenv("RECOVERYLENS_INBOUND_POLL", raising=False)
    assert polling_enabled() is False

    monkeypatch.setenv("RECOVERYLENS_INBOUND_POLL", "1")
    assert polling_enabled() is True


def test_the_interval_has_a_floor(monkeypatch):
    """A one-second poll would burn API calls and add nothing — a carer does
    not reply faster than the network."""
    monkeypatch.setenv("RECOVERYLENS_INBOUND_POLL_SECONDS", "1")
    assert poll_interval_seconds() >= 5

    monkeypatch.setenv("RECOVERYLENS_INBOUND_POLL_SECONDS", "not a number")
    assert poll_interval_seconds() == 10
