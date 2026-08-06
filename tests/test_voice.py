"""
Safety tests for voice replies.

Run:  pytest tests/test_voice.py -v

The property under test throughout: a transcript the carer has not confirmed
never becomes a recorded clinical answer, and never silently closes a check-in.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import (  # noqa: E402
    CONFIRMATION_WINDOW, ConfirmationState, GOOD_CONFIDENCE, MIN_CONFIDENCE,
    NullSpeech, Transcript, build_speech_provider, compose_discarded,
    compose_escalated_unconfirmed, compose_readback, compose_unusable,
    detect_negation_loss, is_confirmation, unconfirmed_escalation_reason,
)

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _t(text, confidence=0.95, **kw):
    return Transcript(text=text, confidence=confidence, provider="test", **kw)


# ------------------------------------------------------- negation-loss guard
@pytest.mark.parametrize("text", [
    "he can move his arm",
    "she can walk to the bathroom",
    "he can speak clearly now",
    "she is taking her tablets",
    "he is eating well",
])
def test_negation_sensitive_phrases_are_flagged(text):
    """These are the transcripts a dropped negation produces. They read as
    reassuring, which is exactly why they must be confirmed."""
    warnings = detect_negation_loss(text)
    assert warnings, f"not flagged: {text!r}"
    assert "negation" in warnings[0].lower()


@pytest.mark.parametrize("text", [
    "he had a fall yesterday",
    "she seems more tired",
    "no change this week",
])
def test_ordinary_transcripts_are_not_flagged(text):
    assert detect_negation_loss(text) == []


def test_flag_text_tells_the_reader_what_to_do():
    w = detect_negation_loss("he can move his arm")[0]
    assert "can't move" in w
    assert "confirm" in w.lower()


# ------------------------------------------------------------- confidence
def test_low_confidence_transcript_is_not_usable():
    assert not _t("he seems worse", confidence=MIN_CONFIDENCE - 0.01).usable


def test_transcript_with_an_error_is_never_usable():
    assert not _t("something", confidence=0.99, error="network").usable


def test_empty_transcript_is_not_usable():
    """An empty transcript must not be read as 'the carer said nothing'."""
    assert not _t("", confidence=0.99).usable


def test_usable_requires_clearing_the_floor():
    assert _t("he had a fall", confidence=MIN_CONFIDENCE).usable
    assert not _t("he had a fall", confidence=MIN_CONFIDENCE - 0.001).usable


def test_high_confidence_is_a_stricter_bar_than_usable():
    t = _t("he had a fall", confidence=(MIN_CONFIDENCE + GOOD_CONFIDENCE) / 2)
    assert t.usable and not t.high_confidence


# ------------------------------------------------------------ confirmation
@pytest.mark.parametrize("text", ["yes", "Yes", "y", "yeah", "correct",
                                  "that's right", "confirm"])
def test_affirmatives_confirm(text):
    assert is_confirmation(text) is True


@pytest.mark.parametrize("text", ["no", "N", "nope", "wrong", "not right"])
def test_negatives_deny(text):
    assert is_confirmation(text) is False


@pytest.mark.parametrize("text", [
    "no wait he also fell",          # a correction, not a denial
    "he seems worse today",          # new information
    "", "   ", "hmm",
])
def test_ambiguous_replies_are_neither(text):
    """A carer adding information mid-correction must not be read as
    confirming the transcript they were correcting."""
    assert is_confirmation(text) is None


def test_short_denial_is_still_a_denial():
    assert is_confirmation("no thats wrong") is False


@pytest.mark.parametrize("text", [
    "yes but he fell yesterday",
    "yes and he's been very confused",
    "no wait he also had a fall",
])
def test_yes_or_no_carrying_new_information_is_ambiguous(text):
    """The dangerous case is the affirmative one: 'yes but he fell yesterday'
    would otherwise confirm the OLD transcript and drop the fall entirely.
    Returning None routes the whole message through triage instead."""
    assert is_confirmation(text) is None


# --------------------------------------------------------------- read-back
def test_readback_quotes_the_transcript_verbatim():
    """Paraphrasing here would defeat the mechanism — the carer must check our
    exact understanding."""
    text = "he can move his arm a bit more today"
    assert f'"{text}"' in compose_readback(_t(text))


def test_readback_asks_a_yes_no_question():
    msg = compose_readback(_t("he had a fall"))
    assert "YES" in msg and "NO" in msg


def test_readback_admits_uncertainty_when_flagged():
    t = _t("he can move his arm", warnings=detect_negation_loss("he can move his arm"))
    assert "not certain" in compose_readback(t).lower()


def test_readback_admits_uncertainty_below_good_confidence():
    msg = compose_readback(_t("he had a fall", confidence=0.80))
    assert "not fully certain" in msg.lower()


def test_confident_clean_readback_makes_no_excuses():
    msg = compose_readback(_t("he had a fall yesterday", confidence=0.98))
    assert "certain" not in msg.lower()


# ------------------------------------------------------------------- state
def test_state_survives_a_json_round_trip():
    s = ConfirmationState(transcript="he had a fall", confidence=0.91,
                          provider="openai", asked_at=NOW.isoformat(),
                          warnings=["w"], audio_ref="rec-1")
    back = ConfirmationState.from_json(s.to_json())
    assert back and back.transcript == s.transcript and back.audio_ref == "rec-1"


def test_state_from_empty_is_none():
    for data in (None, {}, {"transcript": ""}):
        assert ConfirmationState.from_json(data) is None


def test_confirmation_expires_after_the_window():
    old = ConfirmationState(transcript="x", confidence=0.9, provider="p",
                            asked_at=(NOW - CONFIRMATION_WINDOW - timedelta(minutes=1)).isoformat(),
                            warnings=[])
    assert old.expired(now=NOW)


def test_confirmation_within_the_window_has_not_expired():
    fresh = ConfirmationState(transcript="x", confidence=0.9, provider="p",
                              asked_at=(NOW - timedelta(hours=1)).isoformat(),
                              warnings=[])
    assert not fresh.expired(now=NOW)


def test_unparseable_timestamp_does_not_expire():
    """A corrupt timestamp must not silently expire a pending confirmation."""
    s = ConfirmationState(transcript="x", confidence=0.9, provider="p",
                          asked_at="not-a-date", warnings=[])
    assert not s.expired(now=NOW)


# ------------------------------------------------------ unconfirmed escalates
def test_unconfirmed_escalation_quotes_the_transcript_and_marks_it_unverified():
    s = ConfirmationState(transcript="he cant move his arm", confidence=0.8,
                          provider="p", asked_at=NOW.isoformat(), warnings=[])
    reason = unconfirmed_escalation_reason(s)
    assert "unverified" in reason.lower()
    assert "he cant move his arm" in reason


def test_unconfirmed_escalation_carries_the_negation_warning():
    s = ConfirmationState(transcript="he can move his arm", confidence=0.8,
                          provider="p", asked_at=NOW.isoformat(),
                          warnings=detect_negation_loss("he can move his arm"))
    assert "negation" in unconfirmed_escalation_reason(s).lower()


def test_message_to_carer_never_implies_it_was_resolved():
    msg = compose_escalated_unconfirmed().lower()
    assert "care team" in msg
    for banned in ("all good", "no action", "nothing to worry", "resolved"):
        assert banned not in msg


# -------------------------------------------------------------- providers
def test_null_provider_is_the_default(monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_VOICE", raising=False)
    assert isinstance(build_speech_provider(), NullSpeech)


def test_openai_requested_but_unconfigured_falls_back(monkeypatch, capsys):
    monkeypatch.setenv("RECOVERYLENS_VOICE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(build_speech_provider(), NullSpeech)
    assert "not configured" in capsys.readouterr().out


def test_null_transcribe_reports_failure_not_silence():
    """An empty transcript with no error would read as 'the carer said nothing'."""
    t = NullSpeech().transcribe(b"audio", "audio/ogg")
    assert not t.usable and t.error


def test_unusable_message_offers_typing_as_an_alternative():
    """Voice failing must never leave a carer unable to report anything."""
    assert "typ" in compose_unusable().lower()


def test_discard_message_invites_a_retry():
    assert "again" in compose_discarded().lower() or "another" in compose_discarded().lower()
