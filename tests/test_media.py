"""
The public media route.

Why this file is the most security-sensitive one here
----------------------------------------------------
`/media/{token}` is the ONLY route that reads data and answers without a session.
It has to be: Twilio fetches the audio itself and cannot hold a cookie. It is a
deliberate hole in the boundary `test_auth.py` defends, opened one commit after
closing it.

So these tests defend the four things that make the hole narrow:

1. THE TOKEN IS THE CREDENTIAL, and nothing else is. No enumerable id, no listing
   route, nothing to walk.
2. IT EXPIRES IN MINUTES. Twilio fetches within seconds; a window measured in days
   exists only for an attacker's convenience.
3. THE AUDIO NAMES NOBODY. The patient reference is stripped before synthesis, so
   a leaked URL yields generic guidance rather than guidance about a named person.
   This is the mitigation that still holds after the other three have failed.
4. UNKNOWN, EXPIRED AND PURGED ARE INDISTINGUISHABLE. Telling them apart reveals
   whether a URL was ever valid.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from api.media import MAX_SPOKEN_CHARS, MEDIA_TTL, spoken_text


@pytest.fixture
def checkin(db, org_id):
    from api.database import CheckIn, Patient, utcnow

    p = Patient(organisation_id=org_id, patient_ref="ward3-014",
                caregiver_contact="+919999999999", consent_recorded=True)
    db.add(p)
    db.commit()
    c = CheckIn(patient_id=p.id, scheduled_for=utcnow(), reason="Day 3 check-in")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture
def asset(db, checkin):
    from api.media import store_audio

    return store_audio(db, b"OggS-fake-audio-bytes", "audio/ogg",
                       check_in_id=checkin.id, language="ta")


@pytest.fixture
def anon():
    """No session, no carer token. Exactly what Twilio is."""
    from api.main import app

    return TestClient(app)


# ------------------------------------------- 3: the audio must name nobody
def test_the_patient_reference_is_stripped_before_synthesis():
    """The mitigation that survives every other failure.

    Text goes to a consented number. Audio goes to whoever holds an
    unauthenticated URL, so it must not say who it is about.
    """
    body = ("RecoveryLens check-in for ward3-014 — day 3 (Day 3 check-in).\n\n"
            "Keep an eye on their swallowing.\n\n"
            "1. Are they taking their medicines?\n"
            "Reply STOP to stop these messages.")
    spoken = spoken_text(body)

    assert "ward3-014" not in spoken
    assert "RecoveryLens check-in" in spoken
    assert "swallowing" in spoken


def test_instructions_that_make_no_sense_aloud_are_removed():
    """You cannot reply STOP to audio, and "1. 2. 3." read as digits tells a
    listener nothing. The text message still carries all of it."""
    body = ("RecoveryLens check-in — day 3 (Day 3).\n\n"
            "1. Are they taking their medicines?\n"
            "2. Anything new since last time?\n\n"
            "Reply in your own words, or just 'yes no no'.\n"
            "Reply STOP to stop these messages.")
    spoken = spoken_text(body)

    assert "STOP" not in spoken
    assert "Reply in your own words" not in spoken
    assert "taking their medicines" in spoken, "the question itself must survive"


def test_long_messages_are_trimmed_at_a_sentence_boundary():
    body = "RecoveryLens check-in — day 3. " + ("This is a sentence. " * 200)
    spoken = spoken_text(body)
    assert len(spoken) <= MAX_SPOKEN_CHARS
    assert spoken.endswith(".")


# ---------------------------------------------- 1 & 2: the token and its expiry
def test_the_token_alone_fetches_the_audio(anon, asset):
    r = anon.get(f"/media/{asset.token}")
    assert r.status_code == 200
    assert r.content == b"OggS-fake-audio-bytes"
    assert r.headers["content-type"].startswith("audio/ogg")


def test_the_token_is_long_enough_to_be_the_credential(asset):
    """It is the only thing standing between a stranger and the audio."""
    assert len(asset.token) >= 32


def test_audio_is_not_cached_anywhere(anon, asset):
    """Clinical audio must not settle into a CDN, a proxy, or a search index."""
    r = anon.get(f"/media/{asset.token}")
    assert "no-store" in r.headers["cache-control"]
    assert "noindex" in r.headers["x-robots-tag"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_filename_identifies_nobody(anon, asset):
    r = anon.get(f"/media/{asset.token}")
    assert "ward3-014" not in r.headers.get("content-disposition", "")
    assert "checkin.ogg" in r.headers["content-disposition"]


def test_the_ttl_is_minutes_not_days():
    assert MEDIA_TTL <= timedelta(hours=1), (
        "Twilio fetches within seconds. A long window exists only for the "
        "convenience of whoever finds the URL later.")


def test_an_expired_token_stops_working(anon, db, asset):
    from api.database import utcnow

    asset.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert anon.get(f"/media/{asset.token}").status_code == 404


# ------------------------------ 4: unknown, expired and purged look identical
def test_unknown_expired_and_purged_are_indistinguishable(anon, db, asset):
    """Distinguishing them says whether a URL was ever valid, which is precisely
    what a probe is trying to learn."""
    from api.database import utcnow

    unknown = anon.get("/media/definitely-not-a-real-token-at-all")

    asset.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    expired = anon.get(f"/media/{asset.token}")

    token = asset.token
    db.delete(asset)
    db.commit()
    purged = anon.get(f"/media/{token}")

    assert unknown.status_code == expired.status_code == purged.status_code == 404
    assert unknown.json() == expired.json() == purged.json()


def test_one_token_grants_nothing_about_another_asset(anon, db, checkin):
    from api.media import store_audio

    a = store_audio(db, b"first", "audio/ogg", check_in_id=checkin.id)
    b = store_audio(db, b"second", "audio/ogg", check_in_id=checkin.id)

    assert anon.get(f"/media/{a.token}").content == b"first"
    assert anon.get(f"/media/{b.token}").content == b"second"
    assert a.token != b.token


def test_there_is_no_route_that_lists_media():
    """Nothing to enumerate. Checked against the live route table rather than by
    inspection, because a convenience endpoint added later would be exactly the
    kind of thing nobody thinks of as a leak."""
    from conftest import iter_api_routes

    from api.main import app

    media_routes = sorted(path for path, _ in iter_api_routes(app)
                          if path.startswith("/media"))
    assert media_routes == ["/media/{token}"], media_routes


# ----------------------------------------------------------------- purging
def test_purge_deletes_the_bytes_not_just_the_access(db, asset):
    """Expiry stops a URL working; purging stops the audio existing. For a
    recording about a stroke patient's care those are not the same guarantee."""
    from api.database import MediaAsset, utcnow
    from api.media import purge_expired

    asset.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert purge_expired(db) == 1
    assert db.query(MediaAsset).count() == 0


def test_purge_leaves_live_assets_alone(db, asset):
    from api.database import MediaAsset
    from api.media import purge_expired

    assert purge_expired(db) == 0
    assert db.query(MediaAsset).count() == 1


# ------------------------------------------- audio is an enhancement, never a gate
def test_no_public_url_means_text_only_with_a_stated_reason(db, checkin, monkeypatch):
    """The text message is the product. Audio failing must never stop a check-in
    reaching a family, and must never fail silently either."""
    monkeypatch.delenv("RECOVERYLENS_PUBLIC_URL", raising=False)
    from api.media import synthesise_for

    url, record = synthesise_for(db, checkin, "RecoveryLens check-in — day 3.")
    assert url is None
    assert record["attempted"] is False
    assert "RECOVERYLENS_PUBLIC_URL" in record["reason"]


def test_voice_off_means_text_only_with_a_stated_reason(db, checkin, monkeypatch):
    monkeypatch.setenv("RECOVERYLENS_PUBLIC_URL", "https://example.ngrok-free.dev")
    monkeypatch.delenv("RECOVERYLENS_VOICE", raising=False)
    from api.media import synthesise_for

    url, record = synthesise_for(db, checkin, "RecoveryLens check-in — day 3.")
    assert url is None
    assert "voice is not configured" in record["reason"]


def test_a_synthesis_failure_is_reported_not_swallowed(db, checkin, monkeypatch):
    monkeypatch.setenv("RECOVERYLENS_PUBLIC_URL", "https://example.ngrok-free.dev")
    monkeypatch.setenv("RECOVERYLENS_VOICE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")

    from voice import Audio
    import voice

    class Failing:
        name = "fake"

        def synthesise(self, text, language="en"):
            return Audio(data=b"", provider=self.name, error="quota exceeded")

    monkeypatch.setattr(voice, "build_speech_provider", lambda: Failing())
    from api.media import synthesise_for

    url, record = synthesise_for(db, checkin, "RecoveryLens check-in — day 3.")
    assert url is None
    assert record["attempted"] is True
    assert "quota exceeded" in record["reason"]


def test_a_successful_synthesis_returns_a_fetchable_url(db, checkin, anon,
                                                        monkeypatch):
    monkeypatch.setenv("RECOVERYLENS_PUBLIC_URL", "https://example.ngrok-free.dev")
    monkeypatch.setenv("RECOVERYLENS_VOICE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")

    from voice import Audio
    import voice

    class Working:
        name = "fake"

        def synthesise(self, text, language="en"):
            self.spoken = text
            return Audio(data=b"OggS-audio", mime_type="audio/ogg",
                         provider=self.name)

    provider = Working()
    monkeypatch.setattr(voice, "build_speech_provider", lambda: provider)
    from api.media import synthesise_for

    url, record = synthesise_for(
        db, checkin,
        "RecoveryLens check-in for ward3-014 — day 3.\n\nKeep an eye on swallowing.",
        language="ta")

    assert url and url.startswith("https://example.ngrok-free.dev/media/")
    assert record["language"] == "ta"
    assert "ward3-014" not in record["spoken_text"]
    assert "ward3-014" not in provider.spoken

    token = url.rsplit("/", 1)[-1]
    assert anon.get(f"/media/{token}").content == b"OggS-audio"


# ------------------------------------------------ the localhost diagnostic
@pytest.mark.parametrize("url, reachable", [
    ("https://bungee.ngrok-free.dev/media/x", True),
    ("https://recoverylens.onrender.com/media/x", True),
    ("http://localhost:8000/media/x", False),
    ("http://127.0.0.1:8000/media/x", False),
    ("http://192.168.1.10:8000/media/x", False),
    ("http://10.0.0.4/media/x", False),
    ("http://my-laptop.local/media/x", False),
    ("file:///tmp/x.ogg", False),
])
def test_unreachable_media_urls_are_recognised(url, reachable):
    """Not a security control — a diagnostic. Twilio fetches media from its own
    servers, and attaching a localhost URL produces an error that says nothing
    about the real problem, which is always that the tunnel is not configured."""
    from messaging.sender import _publicly_fetchable

    assert _publicly_fetchable(url) is reachable
