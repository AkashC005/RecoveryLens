"""
Safety tests for the messaging layer.

Run:  pytest tests/test_messaging.py -v

Messaging a patient's family is the highest-consequence thing this product does.
A wrong prediction is reviewed by a clinician; a wrong message arrives directly
on someone's phone while they are worried about a relative. These tests are
correspondingly paranoid.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from messaging import (  # noqa: E402
    ConsoleSender, compose_checkin, compose_confirmation,
    compose_stop_confirmation, is_stop_request, looks_like_phone, may_send,
    parse_reply, within_session_window,
)

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
GOOD = {"consent_recorded": True, "contact": "+919876543210",
        "opted_out": False, "last_sent_at": None, "now": NOW}


# --------------------------------------------------------------------- policy
def test_no_message_without_consent():
    d = may_send(**{**GOOD, "consent_recorded": False})
    assert not d and "consent" in d.reason.lower()


def test_opt_out_beats_everything():
    """Opt-out is checked before consent: a withdrawal must not be overridden by
    an older consent record."""
    d = may_send(**{**GOOD, "opted_out": True, "consent_recorded": True})
    assert not d and "opted out" in d.reason.lower()


@pytest.mark.parametrize("contact", [None, "", "   ", "not-a-number",
                                     "carer@example.com", "12"])
def test_unusable_contacts_are_refused(contact):
    assert not may_send(**{**GOOD, "contact": contact})


@pytest.mark.parametrize("contact", ["+919876543210", "+44 7700 900123",
                                     "whatsapp:+14155238886", "07700900123"])
def test_plausible_numbers_pass(contact):
    assert looks_like_phone(contact)


def test_rate_limit_blocks_a_second_message():
    d = may_send(**{**GOOD, "last_sent_at": NOW - timedelta(hours=2)})
    assert not d and "rate limited" in d.reason.lower()


def test_rate_limit_expires():
    assert may_send(**{**GOOD, "last_sent_at": NOW - timedelta(hours=13)})


def test_naive_timestamps_do_not_crash_the_gate():
    """SQLite hands back naive datetimes. A TypeError here would take out the
    scheduler."""
    assert not may_send(**{**GOOD,
                           "last_sent_at": datetime(2026, 8, 3, 11, 0)})


def test_every_refusal_explains_itself():
    for override in ({"consent_recorded": False}, {"opted_out": True},
                     {"contact": None},
                     {"last_sent_at": NOW - timedelta(minutes=5)}):
        d = may_send(**{**GOOD, **override})
        assert not d and len(d.reason) > 20


# ------------------------------------------------------------------- opt-out
@pytest.mark.parametrize("text", [
    "STOP", "stop", "Stop please", "please STOP these messages",
    "unsubscribe", "UNSUBSCRIBE", "cancel", "opt-out", "quit",
])
def test_stop_words_are_recognised(text):
    assert is_stop_request(text)


@pytest.mark.parametrize("text", [
    "he stopped taking his tablets",      # 'stopped', not 'stop'
    "yes no no",
    "she's doing well",
    "",
])
def test_ordinary_replies_are_not_opt_outs(text):
    assert not is_stop_request(text)


# -------------------------------------------------------------------- parsing
def test_explicit_sequence_is_read_in_order():
    p = parse_reply("yes no no")
    assert (p.taking_medication, p.new_symptoms, p.worse_than_last_week) == (True, False, False)
    assert p.confident and p.method == "sequence"


@pytest.mark.parametrize("text", ["y n n", "1. yes 2. no 3. no", "Yes, No, No"])
def test_sequence_variants(text):
    p = parse_reply(text)
    assert p.taking_medication is True
    assert p.method == "sequence"


def test_empty_reply_defaults_to_concerning():
    p = parse_reply("")
    assert p.taking_medication is False
    assert p.new_symptoms is True
    assert p.worse_than_last_week is True
    assert not p.confident


def test_unparseable_reply_defaults_to_concerning():
    """THE rule. An answer we cannot read must not be read as 'fine'."""
    p = parse_reply("hmm not sure really, hard to say")
    assert p.new_symptoms is True
    assert p.worse_than_last_week is True
    assert not p.confident


def test_concern_language_overrides_a_positive_answer():
    """'yes he's taking them but he fell yesterday' has answered the question
    and reported something far more important."""
    p = parse_reply("yes he's taking them but he fell yesterday")
    assert p.new_symptoms is True
    assert p.worse_than_last_week is True
    assert p.method == "keyword"


@pytest.mark.parametrize("text", [
    "he's more confused since Tuesday",
    "she had a fall",
    "his speech is slurred today",
    "not eating much",
    "he's very drowsy",
])
def test_concern_phrases_escalate(text):
    p = parse_reply(text)
    assert p.new_symptoms and p.worse_than_last_week


def test_clearly_positive_reply_is_not_escalated():
    p = parse_reply("all good thanks, taking everything, no change")
    assert p.taking_medication is True
    assert p.new_symptoms is False
    # Still not marked confident — the agent reads it regardless.
    assert not p.confident


def test_full_text_always_survives():
    text = "he's not been himself since Tuesday"
    for p in (parse_reply(text), parse_reply("yes no no"), parse_reply("all good")):
        assert p.free_text == p.free_text.strip()
    assert parse_reply(text).to_submission()["free_text"] == text


def test_submission_shape_matches_the_api():
    keys = set(parse_reply("yes no no").to_submission())
    assert keys == {"taking_medication", "new_symptoms",
                    "worse_than_last_week", "free_text"}


# ------------------------------------------------------------------ composing
def test_checkin_message_has_the_required_elements():
    msg, _ = compose_checkin(day=7, label="One-week review",
                          caregiver_message="Keep an eye on their swallowing.")
    assert "STOP" in msg                      # opt-out, every time
    assert "emergenc" in msg.lower()          # not for emergencies
    assert "1." in msg and "2." in msg and "3." in msg
    assert len(msg) < 700                     # stays scannable


def test_confirmation_never_reassures():
    """The system can raise concern; it cannot establish its absence. No message
    may tell a carer things are fine."""
    for msg in (compose_confirmation(False),
                compose_confirmation(True),
                compose_confirmation(True, "urgent")):
        low = msg.lower()
        for banned in ("all good", "nothing to worry", "they're fine",
                       "no cause for concern", "everything is fine"):
            assert banned not in low


def test_urgent_confirmation_tells_them_not_to_wait():
    msg = compose_confirmation(True, "urgent").lower()
    assert "don't wait" in msg or "straight away" in msg


def test_stop_confirmation_is_unconditional_and_reassuring_about_care():
    msg = compose_stop_confirmation().lower()
    assert "won't get any more" in msg
    assert "does not affect" in msg          # opting out must not feel punitive


def test_long_guidance_is_trimmed_not_truncated_mid_word():
    msg, _ = compose_checkin(day=42, label="Six-week review",
                          caregiver_message="word " * 200)
    assert len(msg) < 900
    assert "wor…" not in msg


# -------------------------------------------------------------------- sending
def test_console_sender_is_the_default_and_sends_nowhere_real(monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_MESSAGING", raising=False)
    from messaging import build_sender
    assert isinstance(build_sender(), ConsoleSender)


def test_twilio_requested_but_unconfigured_falls_back(monkeypatch, capsys):
    """A misconfiguration must not silently stop check-ins going out."""
    monkeypatch.setenv("RECOVERYLENS_MESSAGING", "twilio")
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"):
        monkeypatch.delenv(k, raising=False)
    from messaging import build_sender
    assert isinstance(build_sender(), ConsoleSender)
    assert "not configured" in capsys.readouterr().out


def test_console_sender_records_what_it_would_have_sent():
    s = ConsoleSender(echo=False)
    r = s.send("+919876543210", "hello")
    assert r.ok and s.sent == [("+919876543210", "hello")]


def test_whatsapp_prefix_is_matched_to_the_sender():
    from messaging.sender import _normalise
    assert _normalise("+14155238886", "whatsapp:+1415") == "whatsapp:+14155238886"
    assert _normalise("whatsapp:+14155238886", "+1415") == "+14155238886"


# ------------------------------------------------------------ session window
def test_session_window_closed_without_an_inbound_message():
    assert not within_session_window(None, now=NOW)


def test_session_window_open_within_24h():
    assert within_session_window(NOW - timedelta(hours=23), now=NOW)


def test_session_window_closed_after_24h():
    """A day-42 check-in is far outside any session — production needs an
    approved template, which the sandbox does not enforce."""
    assert not within_session_window(NOW - timedelta(hours=25), now=NOW)
