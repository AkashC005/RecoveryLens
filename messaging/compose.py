"""
RecoveryLens — messaging/compose.py
===================================
Builds the outbound check-in message.

Constraints that shape every choice here
----------------------------------------
This lands on a phone, from an unknown number, possibly at an awkward moment,
read by someone tired and worried. So:

- Short. A long message gets skimmed and the questions get missed.
- The three questions numbered, so a reply of "yes no no" parses cleanly — but
  the message never insists on a format, because a carer who writes a sentence
  instead is giving us MORE information, not less. inbound.py handles both.
- No clinical advice. The generated caregiver guidance from the timeline may be
  included, but it is guideline-derived and already passed the caregiver
  grounding contract.
- An opt-out line every time. Not optional, legally or ethically.
- An explicit "this is not for emergencies" line. Someone in trouble must not
  wait for a reply from an automated check-in.
"""

from __future__ import annotations

MAX_GUIDANCE_CHARS = 300

QUESTIONS = (
    "1. Are they taking their medicines?\n"
    "2. Anything new since last time?\n"
    "3. Are they worse than last week?"
)

FOOTER = (
    "Reply in your own words, or just 'yes no no'.\n"
    "Not for emergencies — if you're worried now, call your doctor or emergency "
    "services.\n"
    "Reply STOP to stop these messages."
)


def compose_checkin(day: int, label: str, caregiver_message: str = "",
                    patient_ref: str | None = None,
                    language: str | None = None) -> tuple[str, dict]:
    """One check-in message, in the carer's language. Returns (body, translation).

    `caregiver_message` is the generated, guideline-grounded text from
    guidance/followup.py. It is included when short enough to keep the whole
    message scannable, and truncated at a sentence boundary rather than mid-word
    if not.

    The WHOLE message is translated as one unit rather than phrase by phrase.
    Translating the fragments separately would produce something grammatical in
    neither language: the questions and the footer read as one piece of writing,
    and word order differs enough that stitching translated parts together
    reliably breaks.

    Everything here is text RecoveryLens wrote — the label, the questions, the
    generated caregiver line. No quoted guideline excerpt reaches this function,
    which is why it can be translated at all. `translate()` enforces that with a
    required `provenance` argument.

    Returns the translation record alongside the body so the caller can store why
    a message went out in English when the patient's language is Tamil. A silent
    fallback to English looks identical to a system that never supported Tamil.
    """
    who = f" for {patient_ref}" if patient_ref else ""
    parts = [f"RecoveryLens check-in{who} — day {day} ({label})."]

    if caregiver_message:
        parts.append(_trim(caregiver_message.strip()))

    parts.append(QUESTIONS)
    parts.append(FOOTER)
    english = "\n\n".join(parts)

    from guidance.translate import translate

    result = translate(english, language or "en", provenance="generated")
    return result.text, result.to_json()


def compose_confirmation(escalated: bool, urgency: str = "routine") -> str:
    """Sent after a reply is processed.

    Never reassures. The system cannot establish that someone is fine — the
    rules and the agent can only raise concern, never clear it — so a message
    saying "all good" would be claiming something nothing in this product
    knows. It confirms receipt, and says what happens next.
    """
    if escalated:
        if urgency == "urgent":
            return ("Thank you. We've passed this to the care team to look at "
                    "straight away.\n\nIf they get worse or you're worried now, "
                    "don't wait for us — call your doctor or emergency services.")
        return ("Thank you. We've passed this to the care team to review.\n\n"
                "If anything changes or you're worried, call your doctor.")
    return ("Thank you — that's recorded.\n\n"
            "If anything changes before the next check-in, call your doctor.")


def compose_stop_confirmation() -> str:
    return ("You won't get any more check-in messages.\n\n"
            "Your care team can turn these back on if you change your mind. "
            "This does not affect any of their care.")


def compose_no_consent_notice() -> str:
    """Never sent. Returned so the reason a send was blocked is legible in logs."""
    return "[blocked] No recorded consent for this patient's contact."


def _trim(text: str, limit: int = MAX_GUIDANCE_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for stop in (". ", "! ", "? "):
        i = cut.rfind(stop)
        if i > limit * 0.5:
            return cut[: i + 1].strip()
    return cut.rsplit(" ", 1)[0].strip() + "…"
