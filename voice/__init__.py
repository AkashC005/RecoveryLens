"""RecoveryLens voice.

Lets a carer reply to a check-in by voice note instead of typing — which matters
because text assumes literacy in the target script, and that assumption fails for
exactly the people this product is meant to reach.

The rule that governs this package: NOTHING SAID BY VOICE IS RECORDED UNTIL THE
CARER CONFIRMS WE HEARD IT RIGHT.

That is not caution for its own sake. Speech recognition drops short function
words most readily, so its characteristic error turns "he can't move his arm"
into "he can move his arm" — fluent, plausible, and wrong in the reassuring
direction, where nothing downstream will catch it. Text replies need no such
check; a carer who typed that sentence meant it.

Three defences, in order:
  1. Read-back confirmation before anything is recorded (confirm.py).
  2. Low ASR confidence escalates rather than being interpreted (speech.py).
  3. Transcripts that may have lost a negation are flagged explicitly.

An unconfirmed transcript escalates to a clinician rather than being discarded.
We know a message exists; we just cannot read it.

NullSpeech is the default — voice must be configured deliberately. English only
for now, and that limitation is real: accuracy falls hardest on the code-switched
speech (English clinical terms inside Tamil or Hindi) that Indian carers actually
use.
"""

from .confirm import (  # noqa: F401
    CONFIRMATION_WINDOW,
    ConfirmationState,
    compose_discarded,
    compose_escalated_unconfirmed,
    compose_readback,
    compose_unusable,
    is_confirmation,
    unconfirmed_escalation_reason,
)
from .speech import (  # noqa: F401
    GOOD_CONFIDENCE,
    MIN_AUDIO_BYTES,
    MIN_CONFIDENCE,
    Audio,
    NullSpeech,
    OpenAISpeech,
    SpeechProvider,
    Transcript,
    build_speech_provider,
    detect_negation_loss,
    looks_hallucinated,
    voice_enabled,
)

__all__ = [
    "Transcript", "Audio", "SpeechProvider", "NullSpeech", "OpenAISpeech",
    "build_speech_provider", "voice_enabled", "detect_negation_loss",
    "MIN_CONFIDENCE", "GOOD_CONFIDENCE", "MIN_AUDIO_BYTES",
    "looks_hallucinated",
    "ConfirmationState", "is_confirmation", "compose_readback",
    "compose_unusable", "compose_discarded", "compose_escalated_unconfirmed",
    "unconfirmed_escalation_reason", "CONFIRMATION_WINDOW",
]
