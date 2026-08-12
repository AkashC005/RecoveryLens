"""
Translation of caregiver messages.

The three refusals this file defends
------------------------------------
1. **Quoted guideline text is never translated.** A translated NICE
   recommendation is our paraphrase wearing NICE's citation — worse than an
   uncited paraphrase, because the citation invites trust the text no longer
   earns. Enforced by a required `provenance` argument, not by convention.

2. **A translation that loses a number or a negation is discarded.** These are
   the two failures a general similarity score cannot see:

       "do NOT stop the tablets"  ->  "stop the tablets"     scores ~0.9
       "take it for 14 days"      ->  "take it for 4 days"   scores ~0.99

   Both are fatal and both look fine to an embedding.

3. **A flagged translation is never sent.** English goes out instead and the
   reason is recorded. An unreadable message is a delivery problem a clinician can
   see; a fluent wrong translation of "call your doctor if she becomes drowsy" is
   a clinical one nobody sees until it matters.

No test here calls a real API. Translation is mocked at `translate._call`, which
is the single seam both directions go through.
"""

import pytest

from guidance.translate import (SUPPORTED, TranslationRefused, _check,
                                translate, translation_enabled)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("RECOVERYLENS_TRANSLATE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def _mock(monkeypatch, forward: str, backward: str):
    """Stub both directions. Call order is forward, then back-translation."""
    calls = {"n": 0}

    def fake(system, user):
        calls["n"] += 1
        return forward if calls["n"] == 1 else backward

    monkeypatch.setattr("guidance.translate._call", fake)
    return calls


# ------------------------------------------------- refusal 1: guideline text
def test_guideline_text_cannot_be_translated():
    """The whole reason `provenance` has no default."""
    with pytest.raises(TranslationRefused, match="no longer a"):
        translate("People who need rehabilitation should receive it from a "
                  "specialist stroke service.", "ta", provenance="guideline")


def test_provenance_is_required_and_not_positional():
    with pytest.raises(TypeError):
        translate("hello", "ta")            # type: ignore[call-arg]


@pytest.mark.parametrize("provenance", ["", "quote", "excerpt", "unknown", "GENERATED"])
def test_only_the_exact_string_generated_is_accepted(provenance):
    """Not a truthiness check. "GENERATED" is rejected too — a near-miss that
    silently passed would defeat the guard for whoever typed it."""
    with pytest.raises(TranslationRefused):
        translate("hello", "ta", provenance=provenance)


# ------------------------------------------------ refusal 2: numbers and negation
@pytest.mark.parametrize("source, back, expect", [
    ("Take one tablet for 14 days.", "Take one tablet for 4 days.", "14"),
    ("Keep BP below 180/105.", "Keep BP below 105.", "180/105"),
    ("Call the clinic within 24 hours.", "Call the clinic soon.", "24"),
    ("Give 4.5 mg in the morning.", "Give 4 mg in the morning.", "4.5"),
])
def test_a_lost_number_is_caught(source, back, expect):
    warnings = _check(source, back)
    assert warnings, f"{expect} vanished and nothing complained"
    assert expect in " ".join(warnings)


@pytest.mark.parametrize("source, back", [
    ("Do not stop the tablets.", "Stop the tablets."),
    ("She should not drive yet.", "She should drive yet."),
    ("Never skip the evening dose.", "Skip the evening dose."),
    ("Avoid stairs without help.", "Use the stairs with help."),
])
def test_a_dropped_negation_is_caught(source, back):
    """The single most dangerous translation error, and the one a similarity score
    scores highest."""
    warnings = _check(source, back)
    assert warnings
    assert "negation" in " ".join(warnings)


@pytest.mark.parametrize("text", [
    "Take one tablet for 14 days and do not stop early.",
    "Keep BP below 180/105 and call the clinic within 24 hours.",
    "Give 4.5 mg in the morning.",
])
def test_a_faithful_round_trip_raises_nothing(text):
    assert _check(text, text) == []


def test_an_added_negation_is_not_flagged():
    """Only a DROP matters. Languages carry negation differently and a round trip
    can legitimately gain one — "avoid stairs" coming back as "do not use the
    stairs" is correct, and flagging it would train everyone to ignore warnings."""
    assert _check("Avoid the stairs.", "Do not use the stairs.") == []


def test_an_empty_back_translation_is_flagged():
    assert any("empty" in w for w in _check("Take the tablets.", ""))


# ----------------------------------- refusal 3: nothing doubtful is ever sent
def test_a_clean_translation_is_used(monkeypatch, enabled):
    _mock(monkeypatch, "மாத்திரைகளை 14 நாட்கள் எடுத்துக்கொள்ளுங்கள்.",
          "Take the tablets for 14 days.")
    r = translate("Take the tablets for 14 days.", "ta", provenance="generated")

    assert r.mode == "translated"
    assert r.language == "ta"
    assert r.ok is True
    assert "மாத்திரைகளை" in r.text


def test_a_drifted_translation_is_discarded_and_english_sent(monkeypatch, enabled):
    """The property that makes this safe to switch on."""
    _mock(monkeypatch, "मात्रा 4 दिन लें।", "Take the dose for 4 days.")
    source = "Take the dose for 14 days."
    r = translate(source, "hi", provenance="generated")

    assert r.mode == "failed"
    assert r.text == source, "the carer must receive the English, not the drift"
    assert r.language == "en"
    assert r.ok is False
    assert any("14" in w for w in r.warnings)
    assert any("discarded" in w for w in r.warnings)


def test_a_dropped_negation_discards_the_translation(monkeypatch, enabled):
    _mock(monkeypatch, "गोलियाँ बंद कर दें।", "Stop the tablets.")
    source = "Do not stop the tablets."
    r = translate(source, "hi", provenance="generated")

    assert r.mode == "failed"
    assert r.text == source
    assert any("negation" in w for w in r.warnings)


def test_a_provider_failure_falls_back_to_english_with_a_reason(monkeypatch, enabled):
    def boom(system, user):
        raise RuntimeError("rate limited")

    monkeypatch.setattr("guidance.translate._call", boom)
    r = translate("Take the tablets.", "ta", provenance="generated")

    assert r.mode == "failed"
    assert r.text == "Take the tablets."
    assert any("rate limited" in w or "RuntimeError" in w for w in r.warnings)


def test_the_back_translation_is_kept_for_review(monkeypatch, enabled):
    """A clinician must be able to see what the carer was actually told without
    reading Tamil."""
    _mock(monkeypatch, "தமிழ் உரை", "Take the tablets for 14 days.")
    r = translate("Take the tablets for 14 days.", "ta", provenance="generated")
    assert r.back_translation == "Take the tablets for 14 days."
    assert r.to_json()["back_translation"]


# ------------------------------------------------------------------ off by default
def test_translation_is_off_unless_switched_on_and_keyed(monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_TRANSLATE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert translation_enabled() is False

    monkeypatch.setenv("RECOVERYLENS_TRANSLATE", "1")
    assert translation_enabled() is False, "a flag without a key must not count"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert translation_enabled() is True


def test_unconfigured_sends_english_and_says_why(monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_TRANSLATE", raising=False)
    r = translate("Take the tablets.", "ta", provenance="generated")

    assert r.text == "Take the tablets."
    assert r.language == "en"
    assert r.mode == "passthrough"
    # Not silent. A carer receiving English when their record says Tamil is a fact
    # someone needs to be able to discover.
    assert any("not configured" in w for w in r.warnings)


def test_english_needs_no_translation_and_makes_no_call(monkeypatch, enabled):
    def boom(system, user):
        raise AssertionError("English must not be sent to a translator")

    monkeypatch.setattr("guidance.translate._call", boom)
    r = translate("Take the tablets.", "en", provenance="generated")
    assert r.mode == "passthrough"
    assert r.language == "en"


def test_an_unsupported_language_falls_back_rather_than_guessing(monkeypatch, enabled):
    _mock(monkeypatch, "anything", "anything")
    r = translate("Take the tablets.", "fr", provenance="generated")
    assert r.language == "en"
    assert r.mode == "passthrough"
    assert any("unsupported" in w for w in r.warnings)


def test_empty_text_is_a_no_op(monkeypatch, enabled):
    r = translate("   ", "ta", provenance="generated")
    assert r.mode == "passthrough"


def test_only_the_two_planned_languages_plus_english_are_offered():
    """Adding a third needs a native speaker to check the guards behave, so it is
    not a one-line change even though it looks like one."""
    assert set(SUPPORTED) == {"en", "ta", "hi"}


# ----------------------------------------------------- the composed message
def test_the_whole_checkin_message_is_translated_as_one_unit(monkeypatch, enabled):
    """Translating the fragments separately would produce something grammatical in
    neither language: word order differs enough that stitching translated parts
    together reliably breaks."""
    from messaging import compose_checkin

    seen: list[str] = []

    def fake(system, user):
        seen.append(user)
        return "TRANSLATED" if len(seen) == 1 else "Reply STOP to stop. 1. 2. 3."

    monkeypatch.setattr("guidance.translate._call", fake)
    body, record = compose_checkin(day=3, label="Day 3 check-in",
                                   caregiver_message="Keep an eye on swallowing.",
                                   language="ta")

    assert len([s for s in seen if "Translate into Tamil" in s]) == 1, \
        "one forward call for the whole message, not one per fragment"
    assert "Keep an eye on swallowing" in seen[0]
    assert "STOP" in seen[0], "the footer travels with the message"
    assert record["language"] in {"ta", "en"}
    assert body


def test_an_english_patient_gets_the_message_unchanged(monkeypatch, enabled):
    from messaging import compose_checkin

    monkeypatch.setattr("guidance.translate._call",
                        lambda s, u: (_ for _ in ()).throw(
                            AssertionError("no translation for English")))
    body, record = compose_checkin(day=3, label="Day 3 check-in", language="en")
    assert record["mode"] == "passthrough"
    assert "STOP" in body
