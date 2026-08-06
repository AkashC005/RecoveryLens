"""
RecoveryLens — voice/speech.py
==============================
Text-to-speech and speech-to-text, behind protocols.

Same shape as messaging/sender.py, for the same reason: the policy — when to
trust a transcript, when to escalate instead — is what matters, and it has to be
testable without API keys, without audio files, and without network.

NullSpeech is the default. Voice has to be configured deliberately.

The failure mode this module is built around
--------------------------------------------
Automatic speech recognition drops short function words more readily than
content words. So the characteristic error is:

    "he can't move his arm"   ->   "he can move his arm"

The negation vanishes and the meaning inverts. Critically, it inverts in the
REASSURING direction: a patient who cannot move their arm is transcribed as one
who can. Every other safety rule in this codebase assumes the dangerous
direction is under-escalation, and this is a mechanism that produces exactly
that, silently, in fluent-sounding text.

Three defences, in order of importance:

  1. NEVER ACT ON AN UNCONFIRMED TRANSCRIPT. The carer hears or reads back what
     we understood and confirms it before anything is recorded. See
     voice/confirm.py.
  2. LOW CONFIDENCE ESCALATES. Below the threshold we do not interpret at all;
     we flag for a human.
  3. NEGATION-LOSS DETECTION. Transcripts that look like they may have lost a
     negation are marked, because that specific error is both the most likely
     and the most harmful.

Code-switching makes all of this worse — Indian carers routinely mix English
clinical terms into Tamil or Hindi, and accuracy falls hardest exactly there.
English-only for now; that limitation is real and should be stated, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
import os

# Below this, we do not interpret the transcript at all. Chosen deliberately
# high: the cost of asking a carer to repeat themselves is small, and the cost
# of acting on a misheard clinical report is not.
MIN_CONFIDENCE = 0.75

# Above this we still ask for confirmation, but treat the transcript as usable
# for a read-back. Between the two, we read back and say we were unsure.
GOOD_CONFIDENCE = 0.90


@dataclass
class Transcript:
    text: str
    confidence: float
    provider: str
    language: str = "en"
    duration_seconds: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Whether this may be read back to the carer at all."""
        return bool(self.text.strip()) and self.confidence >= MIN_CONFIDENCE and not self.error

    @property
    def high_confidence(self) -> bool:
        return self.confidence >= GOOD_CONFIDENCE


@dataclass
class Audio:
    data: bytes
    mime_type: str = "audio/ogg"
    provider: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.data) and not self.error


class SpeechProvider(Protocol):
    name: str

    def synthesise(self, text: str, language: str = "en") -> Audio: ...
    def transcribe(self, audio: bytes, mime_type: str, language: str = "en") -> Transcript: ...


class NullSpeech:
    """Does no speech. The default.

    `transcribe` returns an explicit failure rather than an empty string —
    an empty transcript could be mistaken for "the carer said nothing", which
    is a very different thing from "we cannot transcribe".
    """

    name = "null"

    def __init__(self) -> None:
        self.synthesised: list[str] = []

    def synthesise(self, text: str, language: str = "en") -> Audio:
        self.synthesised.append(text)
        return Audio(data=b"", provider=self.name,
                     error="No speech provider configured; text only.")

    def transcribe(self, audio: bytes, mime_type: str,
                   language: str = "en") -> Transcript:
        return Transcript(
            text="", confidence=0.0, provider=self.name, language=language,
            error="No speech provider configured; cannot transcribe audio.")


class OpenAISpeech:
    """Whisper for transcription, TTS for synthesis.

    Chosen over self-hosting: Whisper's smallest useful weights plus torch far
    exceed the 512MB deploy target. The API keeps the footprint at one HTTP
    dependency.

    Whisper does not return a confidence score directly. `avg_logprob` from the
    verbose response is used as a proxy — see `_confidence_from`, and note the
    caveat there, because a proxy that silently reads as a probability is worse
    than no score at all.
    """

    name = "openai"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("OpenAISpeech needs OPENAI_API_KEY.")

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=self.api_key)

    def synthesise(self, text: str, language: str = "en") -> Audio:
        try:
            resp = self._client().audio.speech.create(
                model=os.getenv("RECOVERYLENS_TTS_MODEL", "tts-1"),
                voice=os.getenv("RECOVERYLENS_TTS_VOICE", "alloy"),
                input=text, response_format="opus")
            return Audio(data=resp.read(), mime_type="audio/ogg", provider=self.name)
        except Exception as exc:
            return Audio(data=b"", provider=self.name,
                         error=f"{type(exc).__name__}: {exc}"[:200])

    def transcribe(self, audio: bytes, mime_type: str,
                   language: str = "en") -> Transcript:
        import io
        try:
            buf = io.BytesIO(audio)
            buf.name = "reply.ogg"
            resp = self._client().audio.transcriptions.create(
                model=os.getenv("RECOVERYLENS_STT_MODEL", "whisper-1"),
                file=buf, language=language,
                response_format="verbose_json")

            text = (getattr(resp, "text", "") or "").strip()
            confidence = _confidence_from(resp)
            return Transcript(
                text=text, confidence=confidence, provider=self.name,
                language=language,
                duration_seconds=getattr(resp, "duration", None),
                warnings=detect_negation_loss(text))
        except Exception as exc:
            return Transcript(text="", confidence=0.0, provider=self.name,
                              language=language,
                              error=f"{type(exc).__name__}: {exc}"[:200])


def _confidence_from(resp) -> float:
    """Approximate confidence from Whisper's segment log-probabilities.

    THIS IS A PROXY, NOT A CALIBRATED PROBABILITY. Whisper exposes no
    confidence; `avg_logprob` correlates with transcription quality but is not
    a probability of correctness, and mapping it onto 0-1 makes it look like
    one. It is used only to decide whether to trust a transcript enough to read
    it back — never to decide clinical meaning, and never in place of the
    carer's confirmation.

    If a provider with real confidence scores becomes available, replace this.
    """
    segments = getattr(resp, "segments", None) or []
    if not segments:
        return 0.80          # no segment data; mid-range, still below GOOD

    import math
    logprobs = [getattr(s, "avg_logprob", None) for s in segments]
    logprobs = [lp for lp in logprobs if lp is not None]
    if not logprobs:
        return 0.80

    mean = sum(logprobs) / len(logprobs)
    return max(0.0, min(1.0, math.exp(mean)))


# --------------------------------------------------------------------------- #
# Negation-loss detection
# --------------------------------------------------------------------------- #
# Phrases that are grammatical and fluent, but which a dropped negation would
# have produced from a very different original. "he can move his arm" is a
# perfectly ordinary sentence; it is also what you get when ASR eats the "n't"
# in "he can't move his arm".
_NEGATION_SENSITIVE = [
    ("can move", "can't move"),
    ("can walk", "can't walk"),
    ("can speak", "can't speak"),
    ("can talk", "can't talk"),
    ("can swallow", "can't swallow"),
    ("can eat", "can't eat"),
    ("can stand", "can't stand"),
    ("can remember", "can't remember"),
    ("is taking", "isn't taking"),
    ("has taken", "hasn't taken"),
    ("is eating", "isn't eating"),
    ("is sleeping", "isn't sleeping"),
    ("is better", "isn't better"),
    ("he is fine", "he isn't fine"),
    ("she is fine", "she isn't fine"),
]


def detect_negation_loss(text: str) -> list[str]:
    """Flag transcripts where a dropped negation would invert the meaning.

    Deliberately noisy. A false flag costs one extra confirmation question. A
    missed one means recording that a patient can move their arm when they
    cannot — and doing so in the reassuring direction, where nothing downstream
    will catch it.
    """
    low = (text or "").lower()
    return [
        f"Possible dropped negation: heard {phrase!r}, which could have been "
        f"{negated!r}. Confirm with the carer before acting on this."
        for phrase, negated in _NEGATION_SENSITIVE
        if phrase in low
    ]


def build_speech_provider() -> SpeechProvider:
    """OpenAI only when explicitly requested; null otherwise."""
    if os.getenv("RECOVERYLENS_VOICE", "").strip().lower() == "openai":
        try:
            return OpenAISpeech()
        except RuntimeError as exc:
            print(f"[voice] OpenAI requested but not configured ({exc}). "
                  f"Falling back to text only.")
    return NullSpeech()


def voice_enabled() -> bool:
    return os.getenv("RECOVERYLENS_VOICE", "").strip().lower() not in {"", "0", "off", "false"}
