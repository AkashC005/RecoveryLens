"""
RecoveryLens API — main.py
==========================
FastAPI application.

Run locally
-----------
    uvicorn api.main:app --reload --port 8000

Interactive docs at http://localhost:8000/docs — that page alone is a
demonstrable artifact before any frontend exists.
"""

from datetime import timedelta
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from guidance import UnknownTrigger, get_retriever, guidance_registry
from guidance.followup import planner as followup_planner
from guidance.selector import selector as guidance_selector, selector_enabled
from guidance.retrieval import _synthesis_enabled as _synthesis_on
from triage import TriageAgent, agent_enabled

from .triage_tools import DatabaseToolBox
from .webhooks import router as messaging_router

from .database import Assessment, CheckIn, Patient, get_db, init_db, utcnow
from .predictor import predictor
from .schemas import (AssessmentRequest, AssessmentResponse, CheckInResponse,
                      CheckInSubmission, GuidanceAnswer, GuidanceBlock,
                      GuidanceBundle, GuidanceQuestion, PatientSummary)

app = FastAPI(
    title="RecoveryLens API",
    description=(
        "Post-stroke risk prediction with explanations, built on the "
        "International Stroke Trial. Research prototype — advisory only."
    ),
    version="0.1.0",
)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(messaging_router)


@app.on_event("startup")
def startup() -> None:
    init_db()
    predictor.load()

    # The corpus already self-validated on import. This second check catches the
    # other direction of drift: a trigger added to Predictor._guidance() that has
    # no backing content. Failing here beats discovering it when someone clicks
    # the chip during a demo.
    coverage = guidance_registry.coverage_report()
    print(f"Loaded {len(predictor.models)} models. "
          f"Guidance corpus: {coverage['coverage']} triggers covered, "
          f"{coverage['total_entries']} cited entries.")
    if coverage["evidence_gaps"]:
        print(f"  Documented evidence gaps: {', '.join(coverage['evidence_gaps'])}")

    fu = followup_planner.coverage_report()
    print(f"  Follow-up intervals: {fu['guideline_backed']} guideline-backed, "
          f"{fu['total_citations']} citations. "
          f"Operational: {fu['by_basis']['operational']}. "
          f"Trial convention: {fu['by_basis']['trial_convention']}.")
    print("Ready.")


# --------------------------------------------------------------------------- meta
@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "models_loaded": len(predictor.models),
        "ready": predictor.loaded,
    }


@app.get("/api/meta/schema", tags=["meta"])
def form_schema():
    """Field definitions for the frontend form. Keeping this server-side means
    the UI never hardcodes clinical options that might drift from the model."""
    return predictor.schema


@app.get("/api/meta/metrics", tags=["meta"])
def model_metrics():
    """Performance figures for the Evidence screen."""
    return predictor.metrics


# --------------------------------------------------------------------------- guidance
@app.get("/api/guidance", tags=["guidance"])
def guidance_coverage():
    """Corpus coverage, sources used, and sources assessed then rejected.

    This backs the Evidence screen. Publishing what we rejected and why is the
    point — it is the difference between a curated corpus and an arbitrary one.
    """
    return guidance_registry.coverage_report()


@app.get("/api/guidance/{trigger}", response_model=GuidanceBlock, tags=["guidance"])
def guidance_for_trigger(trigger: str):
    """Cited guideline text for one trigger.

    A trigger with status 'evidence_gap' returns 200 with an empty entry list and
    an evidence_note. That is a real answer, not an error: it says no verified
    source makes a recommendation here.
    """
    try:
        return guidance_registry.get(trigger)
    except UnknownTrigger:
        raise HTTPException(
            404, f"Unknown guidance trigger '{trigger}'. "
                 f"Known triggers: {sorted(guidance_registry.triggers)}")


@app.post("/api/guidance/resolve", response_model=GuidanceBundle, tags=["guidance"])
def resolve_guidance(triggers: list[str]):
    """Resolve a list of triggers in one call — for re-rendering a stored
    assessment without re-running the models."""
    return guidance_registry.for_assessment(triggers)


@app.post("/api/guidance/ask", response_model=GuidanceAnswer, tags=["guidance"])
def ask_guidance(q: GuidanceQuestion):
    """Clinician Q&A over the guidance corpus.

    This is the retrieval surface, and it is clinician-facing by design. A
    question with no sufficiently relevant passage returns `answered: false`
    rather than a best guess — see the note in guidance/retrieval.py on why
    refusal is the correct behaviour for a clinical retriever.

    Not for patient use: patient-facing content goes through the deterministic
    path above, which cannot generate text.
    """
    return get_retriever().ask(q.question, top_k=q.top_k)


# --------------------------------------------------------------------------- assess
def _run_assessment(req: AssessmentRequest, db: Session,
                    patient: Patient | None = None) -> AssessmentResponse:
    """Score, persist, and (re)schedule follow-up.

    Shared by both assessment routes. When `patient` is supplied this is a
    re-assessment: pending check-ins are replaced, because the risk profile that
    generated them has changed. Completed check-ins are history and are kept.
    """
    try:
        result = predictor.predict(req)
    except Exception as exc:
        raise HTTPException(500, f"Prediction failed: {exc}") from exc

    # Guidance selection. The deterministic rules in Predictor._guidance() have
    # already run and their output is in result["guidance_triggers"]; that list
    # is passed in as a floor the agent cannot drop below. The agent reads the
    # full picture — including the four deficits the rules never inspect — and
    # decides what matters for this patient, with a rationale per topic.
    #
    # If the agent is off or fails, `select()` returns the rule topics unchanged,
    # so this call is safe to make unconditionally.
    deficits = {k: str(v).split(".")[-1]
                for k, v in req.model_dump().items() if k.startswith("deficit_")}
    selection = guidance_selector.select(
        risks=result["risks"],
        deficits=deficits,
        rule_topics=result["guidance_triggers"],
    )
    result["guidance_triggers"] = selection["triggers"]

    created = patient is None
    if created:
        patient = Patient(
            patient_ref=req.patient_ref,
            caregiver_contact=req.caregiver_contact,
            consent_recorded=bool(req.caregiver_contact),
        )
        db.add(patient)
        db.flush()
    else:
        # Only overwrite contact details if new ones were supplied — a blank
        # field on a re-assessment must not silently erase consent.
        if req.caregiver_contact:
            patient.caregiver_contact = req.caregiver_contact
            patient.consent_recorded = True
        for existing in list(patient.check_ins):
            if existing.completed_at is None:
                db.delete(existing)

    assessment = Assessment(
        patient_id=patient.id,
        inputs=req.model_dump(mode="json"),
        results={"risks": result["risks"]},
        guidance_triggers=result["guidance_triggers"],
    )
    db.add(assessment)
    db.flush()

    now = utcnow()
    for step in result["followup_plan"]:
        db.add(CheckIn(
            patient_id=patient.id,
            scheduled_for=now + timedelta(days=step["day"]),
            reason=step["reason"],
        ))

    db.commit()
    db.refresh(assessment)
    db.refresh(patient)

    return AssessmentResponse(
        assessment_id=assessment.id,
        patient_id=patient.id,
        patient_created=created,
        assessment_number=len(patient.assessments),
        created_at=assessment.created_at,
        # Resolved at response time, not persisted. The corpus is versioned in
        # git; freezing a copy into every assessment row would mean a guideline
        # correction never reaches records already written.
        guidance=guidance_registry.for_assessment(result["guidance_triggers"]),
        guidance_selection=selection,
        # Days come from result["followup_plan"] unchanged. The planner only
        # annotates them with evidence and narratives — it never adds, removes
        # or moves a check-in.
        timeline=followup_planner.build(
            result["followup_plan"], result["guidance_triggers"], result["risks"]),
        **result,
    )


@app.post("/api/assess", response_model=AssessmentResponse, tags=["assessment"])
def assess(req: AssessmentRequest, db: Session = Depends(get_db)):
    """Score a patient at discharge.

    If `patient_ref` matches an existing record, this re-assesses that patient
    rather than creating a duplicate. Without a `patient_ref` there is nothing to
    match on, so a new record is always created.
    """
    existing = None
    if req.patient_ref:
        existing = (db.query(Patient)
                      .filter(Patient.patient_ref == req.patient_ref)
                      .order_by(Patient.created_at.desc())
                      .first())
    return _run_assessment(req, db, patient=existing)


@app.post("/api/patients/{patient_id}/assess", response_model=AssessmentResponse,
          tags=["assessment"])
def reassess(patient_id: int, req: AssessmentRequest,
             db: Session = Depends(get_db)):
    """Re-assess a known patient by id. Use this when the reference is ambiguous
    or absent, so the caller controls exactly which record is updated."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return _run_assessment(req, db, patient=patient)


# --------------------------------------------------------------------------- patients
@app.get("/api/patients", response_model=list[PatientSummary], tags=["patients"])
def list_patients(db: Session = Depends(get_db), limit: int = 100):
    patients = (db.query(Patient)
                  .order_by(Patient.created_at.desc())
                  .limit(limit).all())
    out = []
    for p in patients:
        latest = (sorted(p.assessments, key=lambda a: a.created_at)[-1]
                  if p.assessments else None)
        summary = None
        if latest and latest.results:
            summary = {r["outcome"]: r["tier"] for r in latest.results["risks"]}
        out.append(PatientSummary(
            id=p.id, patient_ref=p.patient_ref, created_at=p.created_at,
            assessment_count=len(p.assessments), latest_tier_summary=summary,
        ))
    return out


@app.get("/api/patients/{patient_id}", tags=["patients"])
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if not p:
        raise HTTPException(404, "Patient not found")
    return {
        "id": p.id,
        "patient_ref": p.patient_ref,
        "created_at": p.created_at,
        "assessments": [
            {"id": a.id, "created_at": a.created_at, "results": a.results,
             "guidance_triggers": a.guidance_triggers}
            for a in sorted(p.assessments, key=lambda a: a.created_at, reverse=True)
        ],
        "check_ins": [
            {"id": c.id, "scheduled_for": c.scheduled_for,
             "completed_at": c.completed_at, "reason": c.reason,
             "escalated": c.escalated, "escalation_reason": c.escalation_reason}
            for c in sorted(p.check_ins, key=lambda c: c.scheduled_for)
        ],
    }


@app.delete("/api/patients/{patient_id}", tags=["patients"])
def delete_patient(patient_id: int, db: Session = Depends(get_db)):
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if not p:
        raise HTTPException(404, "Patient not found")
    db.delete(p)
    db.commit()
    return {"deleted": patient_id}


# --------------------------------------------------------------------------- check-ins
@app.get("/api/checkins/due", response_model=list[CheckInResponse], tags=["follow-up"])
def due_checkins(include_scheduled: bool = False, db: Session = Depends(get_db)):
    """Check-ins ready to send. The scheduler (Sprint 5) polls this.

    `include_scheduled=true` also returns check-ins whose date has not arrived
    yet. Strictly a demo and testing affordance: a fresh assessment schedules its
    first contact for day 3, so without this the carer screen is correctly but
    unhelpfully empty for three days. The scheduler must never pass it — sending
    a day-90 check-in on day 1 would be worse than sending none.
    """
    query = db.query(CheckIn).filter(CheckIn.completed_at.is_(None))
    if not include_scheduled:
        query = query.filter(CheckIn.scheduled_for <= utcnow())
    rows = query.order_by(CheckIn.scheduled_for).all()
    return [CheckInResponse(
        id=c.id, patient_id=c.patient_id, scheduled_for=c.scheduled_for,
        completed_at=c.completed_at, escalated=c.escalated, responses=c.responses,
    ) for c in rows]


@app.post("/api/checkins/{checkin_id}/respond", tags=["follow-up"])
def submit_checkin(checkin_id: int, sub: CheckInSubmission,
                   db: Session = Depends(get_db)):
    """Caregiver response. Escalation rules are deliberately conservative —
    a missed escalation costs more than a false alarm."""
    c = db.query(CheckIn).filter(CheckIn.id == checkin_id).first()
    if not c:
        raise HTTPException(404, "Check-in not found")

    # STEP 1 — the boolean rules. These run first, always, and their output is
    # never revisited. Everything below can only add to this list.
    reasons = []
    if not sub.taking_medication:
        reasons.append("Medication not being taken")
    if sub.new_symptoms:
        reasons.append("New symptoms reported")
    if sub.worse_than_last_week:
        reasons.append("Condition reported as worsening")

    # STEP 2 — the agent reads the free text, which until now was stored and
    # never looked at. It may add reasons; it has no capability to remove any.
    # If it is disabled, unconfigured, or fails outright, `finalise()` returns
    # the rule result unchanged.
    result = TriageAgent(DatabaseToolBox(db)).run(
        free_text=sub.free_text or "",
        rule_escalations=reasons,
        patient_id=c.patient_id,
    ).finalise()

    c.responses = sub.model_dump()
    c.completed_at = utcnow()
    c.escalated = result["escalated"]
    c.escalation_reason = result["escalation_reason"]
    c.urgency = result["urgency"]
    c.triage = result
    db.commit()

    return {
        "check_in_id": c.id,
        "escalated": c.escalated,
        "escalation_reason": c.escalation_reason,
        "urgency": c.urgency,
        "triage_mode": result["mode"],
        "rule_reasons": result["rule_reasons"],
        "agent_reasons": result["agent_reasons"],
        "message": ("A clinician will review this response."
                    if c.escalated else "Thank you — recorded."),
    }


@app.get("/api/escalations", tags=["follow-up"])
def escalations(db: Session = Depends(get_db)):
    """Clinician inbox.

    Returns the triage record alongside the flag so a clinician can see WHY it
    was raised and by what — the boolean rules or the agent — plus which tools
    the agent consulted. An escalation a clinician cannot audit is one they will
    learn to ignore.
    """
    rows = (db.query(CheckIn)
              .filter(CheckIn.escalated.is_(True))
              .order_by(CheckIn.completed_at.desc()).all())
    return [{
        "check_in_id": c.id,
        "patient_id": c.patient_id,
        "patient_ref": c.patient.patient_ref if c.patient else None,
        "completed_at": c.completed_at,
        "reason": c.escalation_reason,
        "urgency": c.urgency or "routine",
        "responses": c.responses,
        "triage": c.triage,
    } for c in rows]


@app.get("/api/triage/status", tags=["meta"])
def ai_status():
    """Which model-driven features are actually live.

    Every one is off by default and degrades silently to deterministic
    behaviour, which is correct engineering and terrible for debugging: a
    disabled feature looks exactly like one that ran and found nothing. This
    endpoint is the single place to see what is really switched on.
    """
    key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    features = {
        "guidance_selection": {
            "enabled": selector_enabled(),
            "env": "RECOVERYLENS_GUIDANCE_AGENT",
            "what": "Agent chooses which guidance topics apply, and why.",
            "fallback": "Deterministic if/else on risk tiers and 4 of 8 deficits.",
        },
        "checkin_narratives": {
            "enabled": _synthesis_on(),
            "env": "RECOVERYLENS_LLM_SYNTHESIS",
            "what": "Generates clinician and caregiver text for each check-in, "
                    "and prose answers in the Ask box.",
            "fallback": "Retrieved passages returned verbatim.",
        },
        "triage_agent": {
            "enabled": agent_enabled(),
            "env": "RECOVERYLENS_TRIAGE_AGENT",
            "what": "Reads caregiver free text and decides whether a clinician "
                    "should see it.",
            "fallback": "Three boolean checks only; free text is not read.",
        },
    }
    live = [k for k, v in features.items() if v["enabled"] and key]

    return {
        "api_key_configured": key,
        "features": features,
        "live": live,
        "all_live": len(live) == len(features),
        "note": ("Predictions are always the trained models — never an LLM. "
                 "Guidance excerpts are always verbatim and cited. Agents choose "
                 "and explain; they do not write clinical text."),
    }