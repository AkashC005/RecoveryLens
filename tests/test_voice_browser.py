"""
The browser voice path, through HTTP.

Why this file exists separately from test_voice.py
-------------------------------------------------
`test_voice.py` tests the voice package. This tests the claim that the browser
path is not a second implementation of it — that a recording uploaded from a web
page passes through the same confidence gate, the same negation-loss detection and
the same read-back requirement as one sent over WhatsApp.

That claim is the whole justification for the browser path existing. A demo-only
shortcut around the safety gates would be worse than having no UI at all, because
it would be the version people see.

The gates, and what each is for
-------------------------------
1. NOTHING IS RECORDED UNTIL CONFIRMED. The transcript is parked in
   `triage["pending_voice"]`, never written to `responses`.
2. LOW CONFIDENCE IS A SIGNAL, NOT A GATE. A hard-to-hear recording is still
   read back, with a warning attached — a human reading their own words is a
   stronger check than a score that measures how sure the model was rather than
   whether it was right. What IS still refused: nothing heard, a provider
   failure, audio too short to contain speech, and text that looks invented from
   silence, which is the one failure a human confirming cannot catch.
3. NEGATION LOSS IS SURFACED. "he can't move his arm" transcribing as "he can
   move his arm" is the characteristic ASR failure and it runs in the reassuring
   direction, so it must reach the read-back.
4. ABANDONMENT ESCALATES. Closing the tab must behave like ignoring the WhatsApp
   read-back: a clinician is told a recording exists that nobody could verify.
"""

import json

import pytest
from fastapi.testclient import TestClient

from voice import GOOD_CONFIDENCE, MIN_CONFIDENCE, Transcript


# `client`, `db` and `org_id` come from conftest.py.


@pytest.fixture
def checkin(db, org_id):
    from api.database import CheckIn, Patient, utcnow

    p = Patient(organisation_id=org_id, patient_ref="voice-01",
                caregiver_contact="+919999999999", consent_recorded=True)
    db.add(p)
    db.commit()
    c = CheckIn(patient_id=p.id, scheduled_for=utcnow(), reason="Day 3 check-in")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture
def carer(client, checkin):
    """An UNAUTHENTICATED client holding only this check-in's access token.

    Every voice test runs as the carer rather than as the signed-in clinician,
    because that is the path production uses: a family member follows a link, with
    no account. Testing it as a clinician would leave the real path unexercised
    and would hide any way the token path bypasses a gate.
    """
    from fastapi.testclient import TestClient

    from api.main import app

    token = client.get(f"/api/checkins/{checkin.id}/link").json()["token"]
    anon = TestClient(app)
    anon.headers.update({"x-checkin-token": token})
    return anon


class FakeSpeech:
    """Stands in for the provider so tests never call an API.

    Substituted at `voice.build_speech_provider`, which is the same seam the
    Twilio path uses — so if the browser endpoint stopped going through the shared
    core, these tests would stop being able to control it and would fail.
    """

    name = "fake"

    def __init__(self, text="", confidence=0.9, warnings=None, error=""):
        self._t = Transcript(text=text, confidence=confidence, provider=self.name,
                             language="en", warnings=warnings or [], error=error)
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, audio, mime_type, language="en"):
        self.calls.append((len(audio), mime_type))
        return self._t

    def synthesise(self, text, language="en"):
        raise AssertionError("the browser path must not synthesise audio")


def _use(monkeypatch, provider):
    import api.webhooks
    import voice

    monkeypatch.setattr(voice, "build_speech_provider", lambda: provider)
    monkeypatch.setattr(api.webhooks, "record_voice_note",
                        api.webhooks.record_voice_note)   # unchanged; explicit
    return provider


def _post_audio(carer, checkin_id, data=b"\x00" * 2048,
                mime="audio/webm;codecs=opus"):
    return carer.post(f"/api/checkins/{checkin_id}/voice", content=data,
                      headers={"content-type": mime})


def _age_the_readback(db, checkin, hours=7):
    """Backdate the parked read-back so it falls outside CONFIRMATION_WINDOW.

    Uses copy.deepcopy deliberately. `dict(checkin.triage)` is a SHALLOW copy, so
    `state["pending_voice"]` stays the same object as the one SQLAlchemy loaded —
    mutating it changes the loaded value too, the new top-level dict compares equal
    to it, and the UPDATE is never issued. The first version of these tests did
    exactly that and failed with the data apparently unchanged.
    """
    import copy
    from datetime import timedelta

    from api.database import utcnow

    db.refresh(checkin)
    state = copy.deepcopy(dict(checkin.triage))
    state["pending_voice"]["asked_at"] = (utcnow() - timedelta(hours=hours)).isoformat()
    checkin.triage = state
    db.commit()


# ------------------------------------------------- gate 1: nothing until confirmed
def test_upload_never_records_the_transcript(carer, db, checkin, monkeypatch):
    _use(monkeypatch, FakeSpeech(text="he seems a bit more tired than last week"))

    body = _post_audio(carer, checkin.id).json()
    assert body["confirmed"] is False
    assert body["usable"] is True
    assert "more tired" in body["transcript"]

    db.refresh(checkin)
    assert checkin.responses is None, "responses must stay empty until confirmed"
    assert checkin.completed_at is None
    assert checkin.triage["pending_voice"]["transcript"].startswith("he seems")


def test_confirming_returns_the_text_but_still_does_not_submit(
        carer, db, checkin, monkeypatch):
    """Confirmation hands the text back for the carer to submit with the form.

    The check-in is completed by exactly one endpoint — `/respond` — so the triage
    agent reads voice and typed text identically and there is no second way to
    finish a check-in.
    """
    _use(monkeypatch, FakeSpeech(text="he fell yesterday getting out of bed"))
    _post_audio(carer, checkin.id)

    body = carer.post(f"/api/checkins/{checkin.id}/voice/confirm",
                       json={"confirmed": True}).json()
    assert body["confirmed"] is True
    assert body["transcript"] == "he fell yesterday getting out of bed"

    db.refresh(checkin)
    assert checkin.responses is None
    assert checkin.completed_at is None
    assert "pending_voice" not in (checkin.triage or {})


def test_rejecting_discards_it_and_returns_no_text(carer, db, checkin, monkeypatch):
    _use(monkeypatch, FakeSpeech(text="something misheard entirely"))
    _post_audio(carer, checkin.id)

    body = carer.post(f"/api/checkins/{checkin.id}/voice/confirm",
                       json={"confirmed": False}).json()
    assert body["confirmed"] is False
    assert body["transcript"] == ""

    db.refresh(checkin)
    assert "pending_voice" not in (checkin.triage or {})


def test_confirm_without_a_pending_transcript_is_rejected(carer, checkin):
    r = carer.post(f"/api/checkins/{checkin.id}/voice/confirm",
                    json={"confirmed": True})
    assert r.status_code == 409


def test_an_expired_readback_cannot_be_confirmed(carer, db, checkin, monkeypatch):
    """The same six-hour window the WhatsApp path uses. An old read-back is not
    accepted just because the browser tab stayed open."""
    _use(monkeypatch, FakeSpeech(text="he has been more confused since tuesday"))
    _post_audio(carer, checkin.id)
    _age_the_readback(db, checkin)

    r = carer.post(f"/api/checkins/{checkin.id}/voice/confirm",
                    json={"confirmed": True})
    assert r.status_code == 409
    assert "expired" in r.json()["detail"].lower()


# --------------------------------------------- gate 2: low confidence is not read
@pytest.mark.parametrize("confidence", [0.0, 0.3, MIN_CONFIDENCE - 0.01])
def test_low_confidence_is_shown_for_checking_not_thrown_away(
        carer, db, checkin, monkeypatch, confidence):
    """Reversed deliberately — see test_voice.py for the full reasoning.

    A human reading their own words back is a stronger check than the model's
    confidence score, which measures how sure it was rather than whether it was
    right. Refusing to show the transcript threw away information the carer had
    already given us and made them type it again.

    Still parked, still unconfirmed, still nothing written to `responses`.
    """
    _use(monkeypatch, FakeSpeech(text="he can move his arm now",
                                 confidence=confidence))

    body = _post_audio(carer, checkin.id).json()
    assert body["usable"] is True
    assert body["low_confidence"] is True
    assert body["transcript"] == "he can move his arm now"
    assert body["confirmed"] is False, "shown is not the same as recorded"

    db.refresh(checkin)
    assert checkin.triage["pending_voice"]["transcript"] == "he can move his arm now"
    assert checkin.responses is None
    assert checkin.completed_at is None


def test_a_hallucinated_transcript_is_still_refused(carer, db, checkin, monkeypatch):
    """The one refusal left, and the one a human cannot be relied on to catch."""
    _use(monkeypatch, FakeSpeech(text="Thank you for watching!", confidence=0.95))

    body = _post_audio(carer, checkin.id).json()
    assert body["usable"] is False
    assert body["transcript"] == ""

    db.refresh(checkin)
    assert "pending_voice" not in (checkin.triage or {})
    assert checkin.triage["voice_attempts"] == 1


def test_audio_too_short_is_refused_before_transcribing(carer, db, checkin,
                                                        monkeypatch):
    """Whisper invents sentences from silence, so a clip too short to contain
    speech is refused before the provider is even asked."""
    from voice import MIN_AUDIO_BYTES

    provider = _use(monkeypatch, FakeSpeech(text="he fell down the stairs",
                                            confidence=0.99))
    body = _post_audio(carer, checkin.id, data=b"\x00" * (MIN_AUDIO_BYTES - 1)).json()

    assert body["usable"] is False
    assert provider.calls == [], "the provider must not be called at all"
    db.refresh(checkin)
    assert "too short" in checkin.triage["last_voice_error"]


def test_repeated_failures_are_counted(carer, db, checkin, monkeypatch):
    _use(monkeypatch, FakeSpeech(text="[music]", confidence=0.2))
    for _ in range(3):
        _post_audio(carer, checkin.id)
    db.refresh(checkin)
    assert checkin.triage["voice_attempts"] == 3


def test_a_provider_error_does_not_become_an_empty_confirmation(
        carer, db, checkin, monkeypatch):
    _use(monkeypatch, FakeSpeech(text="", confidence=0.99, error="rate limited"))
    body = _post_audio(carer, checkin.id).json()
    assert body["usable"] is False
    db.refresh(checkin)
    assert "rate limited" in checkin.triage["last_voice_error"]


# ------------------------------------------ gate 3: negation loss reaches the user
def test_negation_loss_warnings_are_surfaced_to_the_readback(
        carer, db, checkin, monkeypatch):
    """"he can't move his arm" transcribing as "he can move his arm" is the
    characteristic ASR error, and it runs in the REASSURING direction — so it
    would not be caught by anything downstream. The read-back is the only place
    a human can notice it, so the warnings have to reach the client."""
    from voice import detect_negation_loss

    text = "he can move his arm and he is not confused"
    warnings = detect_negation_loss(text)
    assert warnings, "fixture no longer triggers the detector — pick another phrase"

    _use(monkeypatch, FakeSpeech(text=text, warnings=warnings))
    body = _post_audio(carer, checkin.id).json()

    assert body["warnings"] == warnings
    db.refresh(checkin)
    assert checkin.triage["pending_voice"]["warnings"] == warnings


# ------------------------------------------------- gate 4: abandonment escalates
def test_an_abandoned_browser_transcript_escalates_like_an_ignored_readback(
        carer, db, checkin, monkeypatch):
    """Closing the tab must not quietly close the check-in.

    This is the property that makes the browser path safe to demonstrate: it fails
    the same way the WhatsApp path fails. `escalate_unconfirmed_voice` is the
    scheduler sweep, and it does not know or care which path parked the transcript.
    """
    from api.webhooks import escalate_unconfirmed_voice

    _use(monkeypatch, FakeSpeech(text="he has been more confused since tuesday"))
    _post_audio(carer, checkin.id)
    _age_the_readback(db, checkin)

    escalated = escalate_unconfirmed_voice(db)
    assert checkin.id in escalated

    db.refresh(checkin)
    assert checkin.escalated is True
    assert checkin.escalation_reason
    # The clinician must be told the recording itself is not available, rather
    # than being left to look for a link that was never created.
    assert "not retained" in json.dumps(checkin.triage)


# --------------------------------------------------------------- request handling
def test_a_completed_checkin_refuses_new_audio(carer, db, checkin, monkeypatch):
    from api.database import utcnow

    checkin.completed_at = utcnow()
    db.commit()
    _use(monkeypatch, FakeSpeech(text="anything"))
    assert _post_audio(carer, checkin.id).status_code == 409


def test_a_carer_token_reveals_nothing_about_other_checkins(
        carer, db, checkin, org_id, monkeypatch):
    """A mismatched id gets the same 403 whether the target exists or not.

    This is why it is 403 rather than 404 here. For a CLINICIAN, a cross-org read
    returns 404 so that a 403 cannot confirm the record exists elsewhere. For a
    carer the reasoning inverts: their token is valid, it simply is not for this
    check-in, and answering identically for "exists but not yours" and "does not
    exist" is what closes the oracle.
    """
    from api.database import CheckIn, utcnow

    _use(monkeypatch, FakeSpeech(text="anything"))

    real_other = CheckIn(patient_id=checkin.patient_id, scheduled_for=utcnow(),
                         reason="Day 6 check-in")
    db.add(real_other)
    db.commit()
    db.refresh(real_other)

    exists = _post_audio(carer, real_other.id)
    absent = _post_audio(carer, 99999)

    assert exists.status_code == absent.status_code == 403
    assert exists.json()["detail"] == absent.json()["detail"]


def test_empty_body_is_rejected(carer, checkin, monkeypatch):
    _use(monkeypatch, FakeSpeech(text="anything"))
    assert _post_audio(carer, checkin.id, data=b"").status_code == 400


def test_oversized_recording_is_rejected(carer, checkin, monkeypatch):
    from api.webhooks import MAX_AUDIO_BYTES

    _use(monkeypatch, FakeSpeech(text="anything"))
    r = _post_audio(carer, checkin.id, data=b"\x00" * (MAX_AUDIO_BYTES + 1))
    assert r.status_code == 413


def test_the_mime_type_reaches_the_provider(carer, checkin, monkeypatch):
    """Whisper uses the container type; sending webm as wav degrades accuracy
    silently, which is the worst way for it to degrade."""
    provider = _use(monkeypatch, FakeSpeech(text="he is doing well today"))
    _post_audio(carer, checkin.id, mime="audio/ogg;codecs=opus")
    assert provider.calls[-1][1] == "audio/ogg", "codecs parameter should be stripped"


def test_voice_off_reports_itself_rather_than_pretending(carer, checkin, monkeypatch):
    """With RECOVERYLENS_VOICE unset the provider is NullSpeech: it transcribes
    nothing at confidence 0. The endpoint must say voice is not configured, or the
    UI shows "we could not hear that" for a feature that was never switched on —
    which is exactly the confusion that hid this whole feature for weeks."""
    body = _post_audio(carer, checkin.id).json()
    assert body["voice_configured"] is False
    assert body["usable"] is False


def test_high_confidence_still_requires_confirmation(carer, db, checkin,
                                                     monkeypatch):
    """There is no confidence high enough to skip the read-back. Whisper's
    confidence is a proxy derived from log-probabilities, and a fluent
    mis-transcription scores well."""
    _use(monkeypatch, FakeSpeech(text="everything is fine",
                                 confidence=min(0.999, GOOD_CONFIDENCE + 0.2)))
    body = _post_audio(carer, checkin.id).json()
    assert body["confirmed"] is False
    db.refresh(checkin)
    assert checkin.responses is None
