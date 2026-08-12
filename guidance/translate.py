"""
RecoveryLens — guidance/translate.py
====================================
Caregiver messages in Tamil and Hindi.

Why translation is not a formatting concern
-------------------------------------------
Text assumes literacy in the target script, which is exactly the assumption that
fails for an elderly or low-literacy carer in an Indian district. Translation is
the difference between a UK-guideline demo and something a family can act on.

But a translation is a REWRITE, and a rewrite of clinical text can invert its
meaning as thoroughly as a dropped negation in speech recognition. So this module
is built around three refusals.

1. QUOTED GUIDELINE TEXT IS NEVER TRANSLATED
--------------------------------------------
A translated NICE recommendation is no longer a quotation. It is our paraphrase
wearing NICE's citation, which is worse than an uncited paraphrase because the
citation invites trust the text no longer earns.

Enforced structurally, not by convention: `translate()` requires
`provenance="generated"` and raises on anything else. The same pattern as
`embeddings.embed_texts(input_type=...)` — a required argument that a careless
caller cannot default their way past.

2. A TRANSLATION THAT LOSES A NUMBER OR A NEGATION IS DISCARDED
--------------------------------------------------------------
Back-translation is checked, but not with a general similarity score. General
similarity is reassuring and blind to the two failures that actually matter:

    "call the clinic if this happens"  ->  "call the clinic if this happens"
    "do NOT stop the tablets"          ->  "stop the tablets"          <-- fatal
    "take it for 14 days"              ->  "take it for 4 days"        <-- fatal

Both score highly on any embedding or token overlap. So the guards are specific:
every number present before must be present after, and negation count must not
fall. This is the same reasoning as `_strip_refs` in `ingest.py`, which abandons
a footnote strip rather than risk turning 4.5 hours into 4 hours.

3. A FLAGGED TRANSLATION IS NEVER SENT
--------------------------------------
On drift, failure, or a missing key, the ENGLISH original is sent and the reason
is recorded for the clinician. An unreadable message is a delivery problem the
clinician can see and fix. A confidently wrong translation of "call your doctor
if she becomes drowsy" is a clinical one that nobody sees until it matters.

Clinician-facing text stays English throughout, always.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1200

# The two languages the font stack already supports (`Noto Sans Tamil` is loaded
# in web/src/index.css). Adding a third is a data change, not a code change — but
# it does need a native speaker to check the back-translation guards behave, so it
# is not a one-line change either.
SUPPORTED = {
    "en": "English",
    "ta": "Tamil",
    "hi": "Hindi",
}

# Any digit run, including decimals and ratios: "14", "4.5", "180/105".
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*(?:/\d+(?:[.,]\d+)*)?")

# English negations. Counted rather than matched positionally, because word order
# differs in every target language and position tells us nothing.
_NEGATION = re.compile(
    r"\b(not|no|never|don't|do not|doesn't|does not|won't|will not|cannot|"
    r"can't|shouldn't|should not|mustn't|must not|without|stop|avoid|"
    r"unless)\b", re.IGNORECASE)


class TranslationRefused(ValueError):
    """Raised when a caller asks for something this module must not do."""


@dataclass
class Translation:
    """What was sent, in what language, and whether we trusted the translation."""
    text: str
    language: str
    source_text: str
    mode: str = "passthrough"      # translated | passthrough | failed
    back_translation: str = ""
    warnings: list[str] = field(default_factory=list)
    provider: str = ""

    @property
    def ok(self) -> bool:
        """True only when a translation happened AND passed every guard."""
        return self.mode == "translated" and not self.warnings

    def to_json(self) -> dict:
        return {
            "language": self.language, "mode": self.mode,
            "warnings": self.warnings, "provider": self.provider,
            # The back-translation is kept so a clinician reviewing a message can
            # see what the carer was actually told, without reading Tamil.
            "back_translation": self.back_translation,
        }


def translation_enabled() -> bool:
    """Off unless explicitly switched on AND a key exists.

    Same posture as every other model-backed feature: absent configuration means
    English, not a crash and not a silent half-translation.
    """
    flag = os.getenv("RECOVERYLENS_TRANSLATE", "").strip().lower()
    return (flag in {"1", "true", "yes"}
            and bool(os.getenv("ANTHROPIC_API_KEY", "").strip()))


# --------------------------------------------------------------------- prompts
_SYSTEM = """You translate short messages sent to the family carer of a stroke \
patient in India. You are translating a message we wrote, not a clinical \
guideline.

Rules, in order of importance:

1. Translate meaning exactly. Never add advice, reassurance, urgency or \
explanation that is not in the source. Never remove any of those either.
2. Every number, dose, day count and time must appear unchanged. If the source \
says 14 days, the translation says 14 days.
3. Every negation must survive. "Do not stop the tablets" must not become \
"stop the tablets". This is the single most dangerous error you can make.
4. Keep it plain and short. The reader is tired, worried, and may not have \
finished school. Prefer everyday words over medical register.
5. Keep English medical terms in English where a carer would recognise them \
that way, which is normal in Indian speech — e.g. "tablets", "BP", "physio".
6. Reply with the translation ONLY. No preamble, no notes, no alternatives, \
no quotation marks around it."""

_BACK_SYSTEM = """You translate into English. Reply with the English translation \
ONLY — no preamble, no notes, no explanation.

Translate literally, preserving errors. Do NOT correct, improve, or smooth the \
text. If the input says something odd or wrong, your English must say the same \
odd or wrong thing. This translation is being used to check another translation \
for mistakes, so a helpful correction would hide exactly what we are looking \
for."""


# ----------------------------------------------------------------------- guards
def _numbers(text: str) -> list[str]:
    return [n.replace(",", ".") for n in _NUMBER.findall(text)]


def _check(source: str, back: str) -> list[str]:
    """Compare the original with its round trip. Returns human-readable warnings.

    Deliberately narrow. This does not try to judge whether the translation is
    *good* — a model cannot reliably grade its own output, and a general
    similarity score is high for exactly the errors that matter. It asks two
    questions that have objective answers.
    """
    warnings: list[str] = []

    lost = [n for n in _numbers(source) if n not in _numbers(back)]
    if lost:
        warnings.append(
            f"numbers missing from the round trip: {', '.join(sorted(set(lost)))}")

    before, after = len(_NEGATION.findall(source)), len(_NEGATION.findall(back))
    if after < before:
        warnings.append(
            f"negation may have been dropped ({before} in the original, "
            f"{after} after the round trip)")

    if not back.strip():
        warnings.append("back-translation was empty, so nothing could be checked")

    return warnings


# ------------------------------------------------------------------- translate
def translate(text: str, language: str, *, provenance: str) -> Translation:
    """Translate a message we wrote. Falls back to English on any doubt.

    `provenance` is REQUIRED and must be "generated". There is no default,
    because the one thing this module must never do is translate a quoted
    guideline excerpt, and a defaulted argument is how that eventually happens.
    Pass "guideline" and it raises, loudly, at the call site.
    """
    if provenance != "generated":
        raise TranslationRefused(
            f"provenance={provenance!r}. Only text RecoveryLens wrote may be "
            f"translated. A translated guideline excerpt is no longer a "
            f"quotation — it is our paraphrase carrying someone else's citation. "
            f"Send the English excerpt and translate the surrounding message.")

    source = (text or "").strip()
    if language not in SUPPORTED:
        return Translation(text=source, language="en", source_text=source,
                           mode="passthrough",
                           warnings=[f"unsupported language {language!r}"])
    if language == "en" or not source:
        return Translation(text=source, language="en", source_text=source,
                           mode="passthrough")
    if not translation_enabled():
        return Translation(
            text=source, language="en", source_text=source, mode="passthrough",
            warnings=["translation is not configured (RECOVERYLENS_TRANSLATE); "
                      "the carer received English"])

    target = SUPPORTED[language]
    try:
        translated = _call(_SYSTEM, f"Translate into {target}:\n\n{source}")
        back = _call(_BACK_SYSTEM, f"Translate this {target} text into English:"
                                   f"\n\n{translated}")
    except Exception as exc:
        return Translation(
            text=source, language="en", source_text=source, mode="failed",
            warnings=[f"translation failed ({type(exc).__name__}); the carer "
                      f"received English"])

    warnings = _check(source, back)
    if warnings:
        # The whole point of checking. A message that reads fluently and says the
        # opposite of what we wrote is worse than one the carer cannot read.
        return Translation(
            text=source, language="en", source_text=source, mode="failed",
            back_translation=back, provider=_model(),
            warnings=warnings + ["translation discarded; the carer received "
                                 "English"])

    return Translation(text=translated, language=language, source_text=source,
                       mode="translated", back_translation=back,
                       provider=_model())


def _model() -> str:
    return os.getenv("RECOVERYLENS_LLM_MODEL", DEFAULT_MODEL)


def _call(system: str, user: str) -> str:
    """One Anthropic call. No `temperature` — newer models reject it outright.

    Faithfulness comes from the system prompt, not from a sampling parameter.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=_model(), max_tokens=MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "\n".join(parts).strip()


def language_name(code: str | None) -> str:
    return SUPPORTED.get((code or "en"), "English")
