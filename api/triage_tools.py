"""
RecoveryLens API — triage_tools.py
==================================
Concrete ToolBox for the triage agent. This is the only place the agent touches
the database, and it is deliberately read-only.

Why the split exists
--------------------
`triage/agent.py` defines the ToolBox as a Protocol and never imports SQLAlchemy.
That is what lets every safety property in tests/test_triage.py be exercised with
a fake toolbox and no running server. This module supplies the real one.

Read-only, by construction
--------------------------
Every method here reads. None writes, updates or deletes. The agent's only effect
on the world is the flag it returns, which api/main.py merges on top of the rule
escalations — never in place of them. If you add a method here, it should read.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from guidance import get_retriever

from .database import Assessment, CheckIn

# The agent gets a compact view, not the full record. Two reasons: a long tool
# result crowds the context and buries the signal, and the agent has no business
# seeing contact details or raw model inputs to do its job.
MAX_HISTORY = 5
MAX_GUIDANCE_HITS = 3


class DatabaseToolBox:
    """Implements triage.ToolBox against a live session."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ risk
    def get_patient_risk_profile(self, patient_id: int) -> dict:
        latest = (self.db.query(Assessment)
                  .filter(Assessment.patient_id == patient_id)
                  .order_by(Assessment.created_at.desc())
                  .first())
        if not latest or not latest.results:
            return {"available": False,
                    "note": "No assessment on record for this patient."}

        risks = latest.results.get("risks", [])
        return {
            "available": True,
            "assessed_at": str(latest.created_at),
            "risks": [
                {
                    "outcome": r.get("outcome"),
                    "label": r.get("label"),
                    "tier": r.get("tier"),
                    "actionability": r.get("actionability"),
                    # Drivers give the agent something to reason against: a
                    # carer reporting confusion matters more if consciousness
                    # was already a top driver for this patient.
                    "top_drivers": [d.get("factor") for d in r.get("drivers", [])[:3]],
                }
                for r in risks
            ],
            "guidance_triggers": latest.guidance_triggers or [],
        }

    # --------------------------------------------------------------- history
    def get_checkin_history(self, patient_id: int) -> list[dict]:
        rows = (self.db.query(CheckIn)
                .filter(CheckIn.patient_id == patient_id,
                        CheckIn.completed_at.isnot(None))
                .order_by(CheckIn.completed_at.desc())
                .limit(MAX_HISTORY).all())

        out = []
        for c in rows:
            resp = c.responses or {}
            out.append({
                "completed_at": str(c.completed_at),
                "reason": c.reason,
                "taking_medication": resp.get("taking_medication"),
                "new_symptoms": resp.get("new_symptoms"),
                "worse_than_last_week": resp.get("worse_than_last_week"),
                "free_text": (resp.get("free_text") or "")[:300],
                "escalated": bool(c.escalated),
                "escalation_reason": c.escalation_reason,
            })
        return out

    # -------------------------------------------------------------- guidance
    def search_guidance(self, query: str) -> list[dict]:
        """Reuses the existing retriever, including its refusal behaviour.

        If nothing clears the relevance floor this returns an empty list. The
        agent's system prompt tells it explicitly that an empty result is not
        evidence the carer's concern is unimportant — the corpus covers
        rehabilitation and secondary prevention, not acute presentations.
        """
        try:
            hits = get_retriever().search(query, top_k=MAX_GUIDANCE_HITS)
        except Exception as exc:
            return [{"error": f"{type(exc).__name__}"}]

        return [
            {
                "source": h["source"]["short_title"],
                "section": h["section"],
                "heading": h["heading"],
                "excerpt": h["excerpt"],
                "url": h["url"],
            }
            for h in hits
        ]
