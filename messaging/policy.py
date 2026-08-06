"""
RecoveryLens — messaging/policy.py
==================================
Whether a message may be sent at all. Every outbound message passes through
`may_send()`; there is no other path to the sender.

Why this is a separate module
-----------------------------
Consent, opt-out and rate limiting are not delivery concerns, and if they live
inside the sender they get bypassed the first time someone adds a second send
path. Keeping them here, ahead of transport, means the rules apply to anything
that wants to message a patient's family — the scheduler, a manual resend, a
future reminder feature.

The checks, in order, and why each exists
-----------------------------------------
1. OPT-OUT. Someone who replied STOP is never messaged again, for any reason.
   Checked first because it outranks everything, including consent — an opt-out
   is a withdrawal of consent and must not be re-derivable from an older record.
2. CONSENT. `Patient.consent_recorded` must be true. Under India's DPDP Act, and
   as a matter of basic decency, an automated message to a patient's family
   needs recorded permission. `database.py` already warns about this.
3. CONTACT. A destination that exists and looks plausible.
4. RATE LIMIT. No more than one message per patient per window. Guards against a
   scheduler bug turning into dozens of messages to a worried family — the
   failure mode that would end this product's credibility fastest.

Every refusal returns a reason. A message silently not sent is indistinguishable
from a message that failed, and both look like the system working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

# One check-in per patient per 12 hours. The real schedule is days apart, so this
# only ever fires when something has gone wrong.
RATE_LIMIT_WINDOW = timedelta(hours=12)

# Rough sanity check, not validation. Twilio rejects genuinely malformed numbers
# far better than a regex can; this catches empty strings, placeholder text and
# email addresses sitting in a phone field.
_PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,20}$")


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = PolicyDecision(True)


def looks_like_phone(contact: str | None) -> bool:
    contact = (contact or "").strip().replace("whatsapp:", "")
    return bool(contact) and bool(_PHONE_RE.match(contact))


def may_send(*, consent_recorded: bool, contact: str | None,
             opted_out: bool, last_sent_at: datetime | None,
             now: datetime | None = None,
             window: timedelta = RATE_LIMIT_WINDOW) -> PolicyDecision:
    """The single gate. Order matters — see the module docstring."""
    if opted_out:
        return PolicyDecision(
            False, "Recipient has opted out. No further messages, ever.")

    if not consent_recorded:
        return PolicyDecision(
            False, "No recorded consent for this patient's caregiver contact.")

    if not looks_like_phone(contact):
        return PolicyDecision(
            False, f"No usable phone number on record (got {contact!r}).")

    if last_sent_at is not None:
        now = now or datetime.now(timezone.utc)
        if last_sent_at.tzinfo is None:
            last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
        elapsed = now - last_sent_at
        if elapsed < window:
            remaining = window - elapsed
            return PolicyDecision(
                False,
                f"Rate limited — last message {_short(elapsed)} ago, "
                f"{_short(remaining)} remaining in the window.")

    return ALLOWED


def _short(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
