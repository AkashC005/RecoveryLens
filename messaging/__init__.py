"""RecoveryLens messaging.

Delivers check-ins to carers over WhatsApp or SMS, and turns their replies back
into check-in submissions.

Three rules hold across this package, all enforced in code:

  1. NO MESSAGE WITHOUT PERMISSION. Every send passes policy.may_send() —
     opt-out first, then consent, then a usable number, then rate limiting.
  2. AMBIGUITY ESCALATES. A reply that cannot be parsed confidently produces the
     concerning answer, never the reassuring one (inbound.py).
  3. NOTHING REASSURES. No message tells a carer things are fine. The system can
     raise concern; it cannot establish its absence (compose.py).

The default sender is ConsoleSender — Twilio has to be configured deliberately,
so there is no path where a missing env var results in real messages going out.
"""

from .compose import (  # noqa: F401
    compose_checkin,
    compose_confirmation,
    compose_stop_confirmation,
)
from .inbound import ParsedReply, parse_reply  # noqa: F401
from .policy import PolicyDecision, may_send, looks_like_phone  # noqa: F401
from .sender import (  # noqa: F401
    ConsoleSender,
    SendResult,
    Sender,
    TwilioSender,
    build_sender,
    is_stop_request,
    within_session_window,
)

__all__ = [
    "compose_checkin", "compose_confirmation", "compose_stop_confirmation",
    "parse_reply", "ParsedReply",
    "may_send", "PolicyDecision", "looks_like_phone",
    "Sender", "ConsoleSender", "TwilioSender", "SendResult",
    "build_sender", "is_stop_request", "within_session_window",
]
