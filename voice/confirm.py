"""
RecoveryLens — voice/confirm.py
===============================
Read-back confirmation for voice replies.

Nothing a carer says by voice is recorded until they confirm we heard it right.

Why this exists rather than trusting the transcript
---------------------------------------------------
ASR's characteristic failure is dropping a negation, and that error runs in the
reassuring direction: "he can't move his arm" becomes "he can move his arm". A
fluent, plausible sentence that means the opposite of what was said, in the
direction where nothing downstream will question it.

Text replies do not need this. A carer who typed "he can move his arm" meant it.
A carer whose voice note was transcribed that way may not have.

The state machine
-----------------
    voice note arrives
        |
        +-- unusable transcript ------> ASK TO RESEND (escalate if repeated)
        |
        +-- usable ------> READ BACK, await yes/no
                                |
                                +-- "yes" ----> submit as a normal check-in
                                +-- "no" -----> discard, ask them to resend
                                +-- silence --> ESCALATE (see below)

Why silence escalates
---------------------
A carer recorded a message about a stroke patient and we could not confirm what
it said. Closing the check-in as though nothing happened would discard a
communication we know exists but cannot read. Escalating puts it in front of a
clinician with the audio attached, so a human can listen.

This is the same principle as everywhere else in the codebase: the uncertain
path is the safe one, and it is enforced in code rather than hoped for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from .speech import Transcript

# How long a carer has to confirm before the unconfirmed transcript is escalated.
# Long enough not to nag someone who put their phone down; short enough that a
# real deterioration is not sitting unread overnight.
CONFIRMATION_WINDOW = timedelta(hours=6)

_YES = re.compile(r"^\s*(y|yes|yeah|yep|yup|correct|right|that'?s right|ok|okay|"
                  r"confirm|confirmed)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(n|no|nope|wrong|not right|incorrect|that'?s wrong)\b",
                 re.IGNORECASE)


@dataclass
class ConfirmationState:
    """Persisted on the check-in between the read-back and the reply."""
    transcript: str
    confidence: float
    provider: str
    asked_at: str                      # ISO timestamp
    warnings: list[str]
    audio_ref: str | None = None       # where the recording is kept
    attempts: int = 1

    def to_json(self) -> dict:
        return {
            "transcript": self.transcript, "confidence": self.confidence,
            "provider": self.provider, "asked_at": self.asked_at,
            "warnings": self.warnings, "audio_ref": self.audio_ref,
            "attempts": self.attempts,
        }

    @classmethod
    def from_json(cls, data: dict | None) -> "ConfirmationState | None":
        if not data or not data.get("transcript"):
            return None
        return cls(
            transcript=data["transcript"], confidence=data.get("confidence", 0.0),
            provider=data.get("provider", ""), asked_at=data.get("asked_at", ""),
            warnings=data.get("warnings", []), audio_ref=data.get("audio_ref"),
            attempts=data.get("attempts", 1),
        )

    def expired(self, now: datetime | None = None) -> bool:
        if not self.asked_at:
            return False
        try:
            asked = datetime.fromisoformat(self.asked_at)
        except ValueError:
            return False
        if asked.tzinfo is None:
            asked = asked.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) - asked > CONFIRMATION_WINDOW


# A yes/no answer is short. Anything longer is carrying information, and the
# yes/no at the front is not the point of the message.
MAX_BARE_ANSWER_WORDS = 4


def is_confirmation(text: str) -> bool | None:
    """True = confirmed, False = denied, None = neither.

    Only SHORT replies count as a bare yes or no. A longer one starting with
    yes or no is treated as neither, so the caller handles it as a new message.

    This matters in both directions, and the second is the dangerous one:

        "no wait he also fell"      -> read as a bare denial, the transcript is
                                       discarded AND the fall is lost.
        "yes but he fell yesterday" -> read as confirmation, the OLD transcript
                                       is recorded and the fall is dropped
                                       entirely.

    Both lose new clinical information the carer volunteered. Returning None
    sends the whole reply through the normal path, where the triage agent reads
    it — which is what should happen to a message containing "he fell".
    """
    t = (text or "").strip()
    if not t:
        return None

    if len(t.split()) > MAX_BARE_ANSWER_WORDS:
        return None

    if _NO.match(t):
        return False
    if _YES.match(t):
        return True
    return None


def compose_readback(transcript: Transcript) -> str:
    """The read-back message. Quotes what we heard, verbatim.

    Paraphrasing here would defeat the entire mechanism — the carer must check
    our exact understanding, not a tidied version of it.
    """
    lines = [f'I heard: "{transcript.text.strip()}"']

    if transcript.warnings:
        # Say plainly that we may have misheard, rather than burying it.
        lines.append("I'm not certain I caught that correctly.")
    elif not transcript.high_confidence:
        lines.append("I'm not fully certain I heard that right.")

    lines.append("Is that right? Reply YES to send it to the care team, or NO to "
                 "record again.")
    return "\n\n".join(lines)


def compose_unusable(reason: str = "") -> str:
    return ("Sorry — I couldn't make out that recording.\n\n"
            "Please try again somewhere quieter, or just type your message "
            "instead. Typing is always fine.")


def compose_discarded() -> str:
    return ("Discarded, thanks.\n\n"
            "Send another voice note or type your message whenever you're ready.")


def compose_escalated_unconfirmed() -> str:
    """Sent when an unconfirmed transcript is escalated rather than dropped."""
    return ("We didn't hear back to confirm your last message, so we've passed "
            "it to the care team to listen to.\n\n"
            "If anything has changed or you're worried, call your doctor.")


def unconfirmed_escalation_reason(state: ConfirmationState) -> str:
    """What the clinician sees in the Review tab."""
    base = (f"Voice message not confirmed by the carer within "
            f"{int(CONFIRMATION_WINDOW.total_seconds() // 3600)}h. "
            f'Transcript (unverified): "{state.transcript}"')
    if state.warnings:
        base += f" — {state.warnings[0]}"
    return base
