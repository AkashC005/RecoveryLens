"""RecoveryLens triage.

Reads the caregiver free-text that the check-in endpoint previously discarded,
and decides whether a clinician should see it.

The one thing to understand before changing anything here: escalation is
MONOTONIC. The boolean rules in api/main.py run first and always; the agent can
only add to their output. That is enforced in TriageResult.finalise(), not by
prompt instruction, so the agent's worst failure is a false alarm rather than a
missed deterioration.
"""

from .agent import (  # noqa: F401
    MAX_ITERATIONS,
    TOOL_SCHEMAS,
    URGENCY,
    AgentUnavailable,
    ToolBox,
    ToolCall,
    TriageAgent,
    TriageResult,
    agent_enabled,
)

__all__ = [
    "TriageAgent", "TriageResult", "ToolBox", "ToolCall",
    "TOOL_SCHEMAS", "URGENCY", "MAX_ITERATIONS",
    "AgentUnavailable", "agent_enabled",
]
