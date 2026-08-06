"""
RecoveryLens — messaging/inbound.py
===================================
Turns a carer's free-text reply into a check-in submission.

The rule that governs everything here
-------------------------------------
AMBIGUITY ESCALATES. If a reply cannot be parsed confidently, the parser returns
the answer that raises concern, not the one that settles it.

For each question that means a specific default:

    taking_medication      -> False   (assume medicines are NOT being taken)
    new_symptoms           -> True    (assume there ARE new symptoms)
    worse_than_last_week   -> True    (assume they ARE worse)

Those look pessimistic, and they are, deliberately. Consider the alternative: a
carer replies "he's ok I think, bit tired" and the parser cannot decide. Guessing
"fine" closes the check-in and nobody looks. Guessing "concerning" costs a
clinician a glance at the Review tab. The costs are not symmetric, so the default
should not be either.

This mirrors the monotonic principle used in triage and guidance selection: the
uncertain path must be the safe one, enforced by code rather than hoped for.

The full text always reaches the agent
--------------------------------------
Whatever the parser concludes, the entire reply is passed through as `free_text`
and read by the triage agent. So a carer who ignores the format completely and
writes "he's not been himself since Tuesday" still gets triaged on meaning rather
than on failed keyword matching. The parser is a floor, not the interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

# Ordered longest-first so "not taking" is tested before "taking".
_NEGATIVE = [
    r"\bno\b", r"\bnot\b", r"\bnope\b", r"\bhasn'?t\b", r"\bhaven'?t\b",
    r"\bdidn'?t\b", r"\bisn'?t\b", r"\bnone\b", r"\bnever\b", r"\bnah\b",
    r"\brefus", r"\bstopped\b", r"\bmissed\b", r"\bforgot",
]
_POSITIVE = [
    r"\byes\b", r"\byeah\b", r"\byep\b", r"\byup\b", r"\bok\b", r"\bokay\b",
    r"\bfine\b", r"\bgood\b", r"\bwell\b", r"\btaking\b", r"\btaken\b",
    r"\bsame\b", r"\bbetter\b", r"\bimprov", r"\bno change\b", r"\ball good\b",
]

_NEG_RE = re.compile("|".join(_NEGATIVE), re.IGNORECASE)
_POS_RE = re.compile("|".join(_POSITIVE), re.IGNORECASE)

# Negations that are actually REASSURING. "no change", "nothing new" and friends
# all contain a negation word while meaning the opposite of concern.
#
# Without this, "all good thanks, taking everything, no change" tripped the
# negation check and defaulted to escalating. That error is in the safe
# direction, but it is still an error: a clinician inbox full of false alarms
# gets ignored, and an ignored inbox is worse than no inbox. These phrases are
# stripped before negation is assessed, and counted as positive.
_REASSURING_NEGATIONS = re.compile(
    r"\bno (change|problems?|issues?|concerns?|symptoms?|complaints?|"
    r"trouble|worse|difference)\b"
    r"|\bnothing (new|much|to report|different|unusual)\b"
    r"|\bnot (worse|any different)\b",
    re.IGNORECASE)

# A reply of "1. yes 2. no 3. no" or "yes no no" or "y n n".
_SEQUENCE_RE = re.compile(
    r"^\s*(?:\d[\.\)]?\s*)?([yn]|yes|no)\b[\s,;]*"
    r"(?:\d[\.\)]?\s*)?([yn]|yes|no)\b[\s,;]*"
    r"(?:\d[\.\)]?\s*)?([yn]|yes|no)\b",
    re.IGNORECASE)

# Phrases that mean "something is wrong" regardless of question structure.
_CONCERN_RE = re.compile(
    r"\b(worse|worsen|confus|fell|fall|fallen|weak|slur|speech|pain|dizzy|"
    r"vomit|seizure|fit|headache|breath|chest|swollen|bleed|blood|"
    r"not eating|won'?t eat|not drinking|sleep(ing)? (a lot|all day)|"
    r"unrespons|drowsy|unwell|deteriorat)", re.IGNORECASE)


@dataclass
class ParsedReply:
    taking_medication: bool
    new_symptoms: bool
    worse_than_last_week: bool
    free_text: str
    confident: bool
    method: str          # sequence | keyword | default
    notes: str = ""

    def to_submission(self) -> dict:
        return {
            "taking_medication": self.taking_medication,
            "new_symptoms": self.new_symptoms,
            "worse_than_last_week": self.worse_than_last_week,
            "free_text": self.free_text or None,
        }


def _yn(token: str) -> bool:
    return token.lower() in {"y", "yes"}


def parse_reply(text: str) -> ParsedReply:
    """Best-effort structured reading of a free-text reply.

    Three strategies, tried in order of confidence. Whatever happens, the
    original text survives intact in `free_text`.
    """
    raw = (text or "").strip()

    # Nothing to work with. Every default is the concerning one.
    if not raw:
        return ParsedReply(
            taking_medication=False, new_symptoms=True, worse_than_last_week=True,
            free_text="", confident=False, method="default",
            notes="Empty reply. All answers defaulted to the escalating value.")

    # 1. An explicit three-answer sequence: "yes no no", "y n n", "1. yes 2. no 3. no"
    m = _SEQUENCE_RE.match(raw)
    if m:
        return ParsedReply(
            taking_medication=_yn(m.group(1)),
            new_symptoms=_yn(m.group(2)),
            worse_than_last_week=_yn(m.group(3)),
            free_text=raw, confident=True, method="sequence",
            notes="Answers read in order from an explicit yes/no sequence.")

    # 2. Concern language anywhere in the reply overrides everything else. A
    #    carer who writes "yes he's taking them but he fell yesterday" has
    #    answered the question and reported something far more important.
    concern = _CONCERN_RE.search(raw)
    if concern:
        neg_meds = _NEG_RE.search(raw) is not None
        return ParsedReply(
            taking_medication=not neg_meds,
            new_symptoms=True, worse_than_last_week=True,
            free_text=raw, confident=False, method="keyword",
            notes=f"Concern language detected ({concern.group(0)!r}). Symptom and "
                  f"deterioration answers set to the escalating value regardless "
                  f"of sentence structure.")

    # 3. Plain sentiment. Reassuring negations ("no change", "nothing new") are
    #    removed before negation is assessed, so they do not count against a
    #    reply that is plainly positive. Only a clearly positive reply with no
    #    remaining negation is treated as reassuring, and even then it is not
    #    marked confident.
    reassuring = _REASSURING_NEGATIONS.search(raw) is not None
    stripped = _REASSURING_NEGATIONS.sub(" ", raw)

    positive = (_POS_RE.search(raw) is not None) or reassuring
    negative = _NEG_RE.search(stripped) is not None

    if positive and not negative:
        return ParsedReply(
            taking_medication=True, new_symptoms=False, worse_than_last_week=False,
            free_text=raw, confident=False, method="keyword",
            notes="Reply read as reassuring with no negation. The triage agent "
                  "still reads the full text.")

    return ParsedReply(
        taking_medication=False, new_symptoms=True, worse_than_last_week=True,
        free_text=raw, confident=False, method="default",
        notes="Reply could not be parsed confidently. All answers defaulted to "
              "the escalating value; the triage agent reads the full text.")
