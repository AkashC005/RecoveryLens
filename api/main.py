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

from datetime import timedelta, timezone
import hmac
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from guidance import UnknownTrigger, get_retriever, guidance_registry
from guidance.followup import planner as followup_planner
from guidance.selector import selector as guidance_selector, selector_enabled
from guidance.retrieval import _synthesis_enabled as _synthesis_on
from triage import TriageAgent, agent_enabled

from pydantic import BaseModel

from .auth import (COOKIE_NAME, access_token_for,
                   caregiver_or_clinician_checkin, checkin_by_access_token,
                   clear_session_cookie, create_session, create_user,
                   current_user, dev_login_hint, is_first_user, revoke_session,
                   scoped_checkin, scoped_patient, scoped_patients,
                   set_session_cookie, signup_code_required, verify_password)
from .triage_tools import DatabaseToolBox
from .media import router as media_router
from .webhooks import router as messaging_router

from .database import (Assessment, CheckIn, Patient, SessionLocal, User, get_db,
                       init_db, utcnow)
from .predictor import predictor
from .schemas import (AssessmentRecord, AssessmentRequest, AssessmentResponse,
                      CheckInRecord, CheckInResponse, CheckInSubmission,
                      GuidanceAnswer, GuidanceBlock, GuidanceBundle,
                      GuidanceQuestion, MessagingState, PatientDetail,
                      PatientSummary)

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
app.include_router(media_router)

# A discharge summary is a page or two of text; a text-layer PDF of one is under
# a megabyte. Anything larger is the wrong document.
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

# Held so the shutdown hook can stop it. None when scheduling is disabled.
_scheduler = None


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

    # Background sending. Off unless RECOVERYLENS_SCHEDULER=1 — a job that
    # messages patients' families should never start by accident. Failure to
    # start is logged and swallowed; the API must serve requests regardless.
    global _scheduler
    try:
        from messaging.scheduler import start as start_scheduler
        _scheduler = start_scheduler(SessionLocal)
    except Exception as exc:
        print(f"[scheduler] could not start ({type(exc).__name__}: {exc}). "
              f"Check-ins can still be sent manually.")

    with SessionLocal() as session:
        if is_first_user(session):
            print(f"\n{dev_login_hint()}\n")

    print("Ready.")


@app.on_event("shutdown")
def shutdown() -> None:
    """Stop the scheduler cleanly so a reload does not leave a job thread
    sending messages from a half-dead process."""
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            print("[scheduler] stopped.")
        except Exception:
            pass


# --------------------------------------------------------------------------- meta
@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "models_loaded": len(predictor.models),
        "ready": predictor.loaded,
    }


# --------------------------------------------------------------------------- auth
class SignupRequest(BaseModel):
    email: str
    password: str
    # Optional. Defaults to a workspace named after the account, because most
    # people signing up are one person trying the product, not a hospital.
    organisation: str = ""
    full_name: str = ""
    # Only checked when RECOVERYLENS_SIGNUP_CODE is set. See auth.signup_code_required.
    code: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteRequest(BaseModel):
    """A colleague, into the SAME organisation as the inviter. There is no route
    that creates a user in another organisation, deliberately."""
    email: str
    password: str
    full_name: str = ""


@app.post("/api/auth/signup", tags=["auth"])
def signup(req: SignupRequest, request: Request, response: Response,
           db: Session = Depends(get_db)):
    """Create an account and sign in.

    Open by default. The previous version allowed exactly one account ever — a
    reviewer handed the app had no way in without someone provisioning them, and
    the sign-in screen showed a password box with no way to get a password. A form
    that offers no route to using it is not a security measure, it is a dead end.

    **Registering creates your own organisation**, so what you enter is visible
    only to you until you invite someone into it. That is the privacy model: not a
    per-record permission system, but a private workspace per account, enforced by
    the same `scoped_patients()` filter every patient read already goes through.
    """
    required = signup_code_required()
    if required and not hmac.compare_digest(req.code.strip(), required):
        # Compared in constant time, and refused with the same message whether the
        # code was absent or wrong.
        raise HTTPException(403, "That registration code is not valid.")

    first = is_first_user(db)
    user = create_user(db, email=req.email, password=req.password,
                       organisation=req.organisation, full_name=req.full_name)

    # Adopt patients created before authentication existed — only ever on the
    # very first account, when there is exactly one organisation they could
    # sensibly belong to. On any later signup this would hand one person's records
    # to a stranger who happened to register next.
    adopted = 0
    if first:
        adopted = (db.query(Patient)
                   .filter(Patient.organisation_id.is_(None))
                   .update({"organisation_id": user.organisation_id},
                           synchronize_session=False))
        if adopted:
            db.commit()
            print(f"[auth] adopted {adopted} pre-auth patient(s) into "
                  f"'{user.organisation.name}'.")

    set_session_cookie(response, request, create_session(db, user))
    return {"id": user.id, "email": user.email,
            "organisation_id": user.organisation_id,
            "organisation": user.organisation.name if user.organisation else "",
            "adopted_existing_patients": adopted}


@app.post("/api/auth/login", tags=["auth"])
def login(req: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db)):
    """Sign in.

    A wrong email and a wrong password return the identical error. Distinguishing
    them turns the login form into a way to discover which clinicians have accounts
    here, which is information about staffing at a named hospital.
    """
    user = db.query(User).filter(User.email == req.email.strip().lower()).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    if user.disabled:
        raise HTTPException(403, "This account is disabled.")

    set_session_cookie(response, request, create_session(db, user))
    return {"id": user.id, "email": user.email, "full_name": user.full_name,
            "organisation_id": user.organisation_id}


@app.post("/api/auth/logout", tags=["auth"])
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoke the session server-side, not just client-side.

    Deleting the cookie alone would leave a valid token in whatever copied it.
    This is why sessions are a table and not a JWT.
    """
    token = request.cookies.get(COOKIE_NAME)
    if token:
        revoke_session(db, token)
    clear_session_cookie(response)
    return {"signed_out": True}


@app.get("/api/auth/me", tags=["auth"])
def whoami(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "full_name": user.full_name,
            "organisation_id": user.organisation_id,
            "organisation": user.organisation.name if user.organisation else ""}


@app.get("/api/auth/status", tags=["auth"])
def auth_status(db: Session = Depends(get_db)):
    """Unauthenticated on purpose, and says almost nothing.

    The sign-in screen needs two facts before anyone is signed in: whether to
    offer registration at all, and whether to show a code field. Neither reveals
    anything about who has an account here — no user count, no email, no
    organisation name. An unauthenticated endpoint that names the hospital using
    the software has told a stranger something they should have had to earn.
    """
    return {
        "signup_open": True,
        "signup_code_required": bool(signup_code_required()),
    }


@app.post("/api/auth/invite", tags=["auth"])
def invite(req: InviteRequest, db: Session = Depends(get_db),
           user: User = Depends(current_user)):
    """Add a colleague to the caller's own organisation. No cross-org route exists."""
    created = create_user(db, email=req.email, password=req.password,
                          organisation="", full_name=req.full_name,
                          org_id=user.organisation_id)
    return {"id": created.id, "email": created.email,
            "organisation_id": created.organisation_id}


@app.get("/api/checkins/{checkin_id}/link", tags=["follow-up"])
def checkin_link(checkin_id: int, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """The carer's link for this check-in. Clinician-only to issue.

    Minted on demand. The token is the carer's whole credential, so a clinician
    generating one is an authorised act and is scoped to their organisation.
    """
    c = scoped_checkin(checkin_id, user, db)
    token = access_token_for(db, c)
    return {"check_in_id": c.id, "token": token,
            "path": f"/checkin?token={token}"}


# --------------------------------------------------------------------- autofill
@app.post("/api/extract", tags=["assessment"])
async def extract_discharge_summary(request: Request,
                                    user: User = Depends(current_user)):
    """Read a discharge summary into assessment fields. Creates nothing.

    Accepts a pasted document as `text/plain` or an uploaded `application/pdf`.
    Raw body rather than multipart, for the same reason as the voice endpoint:
    `UploadFile` needs python-multipart, which this app does not install.

    This endpoint deliberately does NOT create a patient or run an assessment.
    It returns values for a form the clinician then reviews — the read-back
    pattern from voice, applied to a document. Autofill is the first feature
    where a model writes into a CLINICAL INPUT rather than a display surface, so
    the human confirmation step is not a nicety.

    Every returned field carries the verbatim quote it came from, and that quote
    was checked against the document before it got here.
    """
    from extraction import extract_from_text, text_from_pdf

    body = await request.body()
    if not body:
        raise HTTPException(400, "No document received.")
    if len(body) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"Document is larger than "
                                 f"{MAX_DOCUMENT_BYTES // (1024 * 1024)}MB.")

    content_type = (request.headers.get("content-type") or "").split(";")[0]
    if "pdf" in content_type:
        try:
            document = text_from_pdf(body)
        except ImportError:
            raise HTTPException(
                501, "PDF upload needs pypdf installed. Paste the text instead.")
        except Exception as exc:
            raise HTTPException(
                400, f"Could not read that PDF ({type(exc).__name__}). If it is a "
                     f"scan rather than a text PDF, paste the text instead.")
        if not document.strip():
            # A scanned PDF is images with no text layer. Saying so beats
            # returning an empty form and letting them wonder.
            raise HTTPException(
                400, "That PDF contains no selectable text — it is probably a "
                     "scan. Paste the text instead.")
    else:
        document = body.decode("utf-8", errors="replace")

    return extract_from_text(document).to_json()


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
                    patient: Patient | None = None,
                    user: User | None = None) -> AssessmentResponse:
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
            # Stamped at creation from the caller. A patient with no organisation
            # is invisible to every scoped query rather than visible to all of
            # them, which is the safe direction for the failure.
            organisation_id=user.organisation_id if user else None,
            patient_ref=req.patient_ref,
            caregiver_contact=req.caregiver_contact,
            consent_recorded=bool(req.caregiver_contact),
            language=req.caregiver_language or "en",
        )
        db.add(patient)
        db.flush()
    else:
        # Only overwrite contact details if new ones were supplied — a blank
        # field on a re-assessment must not silently erase consent.
        if req.caregiver_contact:
            patient.caregiver_contact = req.caregiver_contact
            patient.consent_recorded = True
        if req.caregiver_language:
            patient.language = req.caregiver_language
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
def assess(req: AssessmentRequest, db: Session = Depends(get_db),
           user: User = Depends(current_user)):
    """Score a patient at discharge.

    If `patient_ref` matches an existing record IN THE CALLER'S ORGANISATION, this
    re-assesses that patient rather than creating a duplicate. The scoping matters:
    two hospitals both using "ward3-014" must not collide, and matching across
    organisations would attach one hospital's assessment to another's patient.
    """
    existing = None
    if req.patient_ref:
        existing = (scoped_patients(user, db)
                    .filter(Patient.patient_ref == req.patient_ref)
                    .order_by(Patient.created_at.desc())
                    .first())
    return _run_assessment(req, db, patient=existing, user=user)


@app.post("/api/patients/{patient_id}/assess", response_model=AssessmentResponse,
          tags=["assessment"])
def reassess(patient_id: int, req: AssessmentRequest,
             db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    """Re-assess a known patient by id. Use this when the reference is ambiguous
    or absent, so the caller controls exactly which record is updated."""
    patient = scoped_patient(patient_id, user, db)
    return _run_assessment(req, db, patient=patient, user=user)


# --------------------------------------------------------------------------- patients
@app.get("/api/patients", response_model=list[PatientSummary], tags=["patients"])
def list_patients(db: Session = Depends(get_db), limit: int = 100,
                  user: User = Depends(current_user)):
    patients = (scoped_patients(user, db)
                  .order_by(Patient.created_at.desc())
                  .limit(limit).all())
    now = utcnow()
    out = []
    for p in patients:
        latest = (sorted(p.assessments, key=lambda a: a.created_at)[-1]
                  if p.assessments else None)
        summary = None
        if latest and latest.results and latest.results.get("risks"):
            summary = {r["outcome"]: r["tier"] for r in latest.results["risks"]}

        upcoming = [_aware(c.scheduled_for) for c in p.check_ins
                    if c.completed_at is None and _aware(c.scheduled_for)
                    and _aware(c.scheduled_for) >= now]

        out.append(PatientSummary(
            id=p.id, patient_ref=p.patient_ref, created_at=p.created_at,
            assessment_count=len(p.assessments), latest_tier_summary=summary,
            open_escalations=sum(1 for c in p.check_ins if c.escalated),
            next_check_in=min(upcoming) if upcoming else None,
            consent_recorded=bool(p.consent_recorded),
            opted_out=bool(p.opted_out),
        ))
    return out


WHATSAPP_WINDOW = timedelta(hours=24)


def _aware(dt):
    """SQLite hands back naive datetimes. Everything written here is UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _checkin_status(c: CheckIn, now) -> str:
    """Derived, never stored — storing it would mean keeping it in sync.

    The distinction that matters is `overdue` vs `sent`: a check-in whose date
    has passed with nothing sent is OUR failure (scheduler off, consent missing,
    rate limited), not a carer who has not replied. Collapsing the two hides the
    only one we can act on.
    """
    if c.completed_at is not None:
        return "completed"
    if c.sent_at is not None:
        return "sent"
    if _aware(c.scheduled_for) is not None and _aware(c.scheduled_for) < now:
        return "overdue"
    return "scheduled"


def _messaging_state(p: Patient, db: Session, now) -> MessagingState:
    """Whether this carer can be messaged — decided by the real policy gate.

    `may_send()` is called with exactly the arguments the send endpoint uses, so
    this screen cannot claim a send will succeed when the gate would refuse it.
    """
    from messaging import may_send

    last_sent = (db.query(CheckIn)
                 .filter(CheckIn.patient_id == p.id, CheckIn.sent_at.isnot(None))
                 .order_by(CheckIn.sent_at.desc()).first())

    decision = may_send(
        consent_recorded=bool(p.consent_recorded),
        contact=p.caregiver_contact,
        opted_out=bool(p.opted_out),
        last_sent_at=last_sent.sent_at if last_sent else None,
        now=now,
    )

    contact = (p.caregiver_contact or "").strip()
    inbound = _aware(p.last_inbound_at)
    window_open = inbound is not None and (now - inbound) < WHATSAPP_WINDOW

    if inbound is None:
        note = ("This carer has never messaged us, so no free-form window has "
                "ever opened. On WhatsApp the first contact must be an "
                "approved template.")
    elif window_open:
        left = WHATSAPP_WINDOW - (now - inbound)
        note = (f"Open for another {int(left.total_seconds() // 3600)}h "
                f"{int(left.total_seconds() % 3600 // 60)}m. Free-form messages "
                f"are permitted until it closes.")
    else:
        note = ("Closed — the carer's last message was over 24 hours ago. "
                "Free-form sends will fail with [21654]; a template is required.")

    return MessagingState(
        caregiver_contact_on_file=bool(contact),
        # Last four only. This screen is the one that gets demonstrated on a
        # projector; the full number lives in the database and in the send
        # preview, which is where it is actually needed.
        contact_hint=f"…{contact[-4:]}" if len(contact) >= 4 else None,
        consent_recorded=bool(p.consent_recorded),
        opted_out=bool(p.opted_out),
        opted_out_at=p.opted_out_at,
        opt_out_cleared_at=p.opt_out_cleared_at,
        opt_out_cleared_by=p.opt_out_cleared_by,
        opt_out_cleared_reason=p.opt_out_cleared_reason,
        last_inbound_at=p.last_inbound_at,
        whatsapp_window_open=window_open,
        whatsapp_window_note=note,
        can_send=bool(decision),
        blocked_reason=decision.reason or None,
    )


MIN_CLEAR_REASON = 15


class ClearOptOutRequest(BaseModel):
    """Why a carer's withdrawal is being overridden."""
    reason: str


@app.post("/api/patients/{patient_id}/opt-out/clear",
          response_model=MessagingState, tags=["patients"])
def clear_opt_out(patient_id: int, req: ClearOptOutRequest,
                  db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Let a clinician undo an opt-out, on the record.

    This route did not exist, and its absence was itself a bug: `opted_out` is
    set by any inbound message matching STOP, so a carer who mistypes, or whose
    relative borrows the phone, was silently and permanently removed from
    follow-up with no way back. For a WhatsApp keyword in a second language that
    is not a rare accident.

    It is still the most dangerous route in the app, because the person who
    benefits from messages resuming is the one deciding the withdrawal did not
    count. Three things make it defensible rather than a hole:

      1. A reason is MANDATORY and must be long enough to be a sentence. A
         mandatory free-text field is weak protection, but "I had to type why"
         is a different decision from clicking a button.
      2. The clinician's identity and the time are recorded with it.
      3. `opted_out_at` is left in place, so the record permanently reads
         "withdrew on X, overridden by Y on Z" and never reverts to looking like
         a patient who never objected.

    What this deliberately does NOT do is re-record consent. Clearing an opt-out
    restores the previous state; it does not manufacture a fresh agreement, and
    if consent was never recorded the send gate still refuses.
    """
    patient = scoped_patient(patient_id, user, db)

    if not patient.opted_out:
        raise HTTPException(400, "This patient has not opted out.")

    reason = (req.reason or "").strip()
    if len(reason) < MIN_CLEAR_REASON:
        raise HTTPException(
            400,
            f"Give a reason of at least {MIN_CLEAR_REASON} characters. This is "
            f"an override of a carer's withdrawal and it is recorded against "
            f"your account.")

    patient.opted_out = False
    patient.opt_out_cleared_at = utcnow()
    patient.opt_out_cleared_by = user.email
    patient.opt_out_cleared_reason = reason
    db.commit()

    # Printed, not just stored. During a demo this is the one action that should
    # be visible in the server log without anyone going looking for it.
    print(f"[opt-out] patient {patient.id} cleared by {user.email}: {reason}")

    db.refresh(patient)
    return _messaging_state(patient, db, utcnow())


@app.get("/api/patients/{patient_id}", response_model=PatientDetail,
         tags=["patients"])
def get_patient(patient_id: int, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Everything recorded about one patient.

    This endpoint existed before and returned an unvalidated dict that no
    frontend called. It now returns a declared shape and adds the three things
    that were missing and mattered: the assessment INPUTS (a tier is not
    reviewable without them), the full triage record per check-in (rule reasons
    vs agent reasons vs tool trace), and the messaging state — which is the only
    place a clinician can see that follow-up has silently stopped because
    consent was never recorded or the carer opted out.
    """
    p = scoped_patient(patient_id, user, db)

    now = utcnow()
    check_ins = sorted(p.check_ins, key=lambda c: c.scheduled_for or now)
    assessments = sorted(p.assessments, key=lambda a: a.created_at, reverse=True)

    latest = assessments[0] if assessments else None
    tiers = None
    if latest and latest.results and latest.results.get("risks"):
        tiers = {r["outcome"]: r["tier"] for r in latest.results["risks"]}

    upcoming = [_aware(c.scheduled_for) for c in check_ins
                if c.completed_at is None and _aware(c.scheduled_for)
                and _aware(c.scheduled_for) >= now]

    return PatientDetail(
        id=p.id,
        patient_ref=p.patient_ref,
        created_at=p.created_at,
        messaging=_messaging_state(p, db, now),
        assessments=[
            AssessmentRecord(
                id=a.id, created_at=a.created_at, inputs=a.inputs,
                results=a.results, guidance_triggers=a.guidance_triggers or [])
            for a in assessments
        ],
        check_ins=[
            CheckInRecord(
                id=c.id, scheduled_for=c.scheduled_for, sent_at=c.sent_at,
                completed_at=c.completed_at, reason=c.reason,
                status=_checkin_status(c, now), responses=c.responses,
                escalated=bool(c.escalated),
                escalation_reason=c.escalation_reason,
                urgency=c.urgency or "routine", triage=c.triage)
            for c in check_ins
        ],
        latest_tier_summary=tiers,
        open_escalations=sum(1 for c in check_ins if c.escalated),
        next_check_in=min(upcoming) if upcoming else None,
    )


@app.delete("/api/patients/{patient_id}", tags=["patients"])
def delete_patient(patient_id: int, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    p = scoped_patient(patient_id, user, db)
    db.delete(p)
    db.commit()
    return {"deleted": patient_id}


# --------------------------------------------------------------------------- check-ins
@app.get("/api/checkins/due", response_model=list[CheckInResponse], tags=["follow-up"])
def due_checkins(include_scheduled: bool = False, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Check-ins ready to send. The scheduler (Sprint 5) polls this.

    `include_scheduled=true` also returns check-ins whose date has not arrived
    yet. Strictly a demo and testing affordance: a fresh assessment schedules its
    first contact for day 3, so without this the carer screen is correctly but
    unhelpfully empty for three days. The scheduler must never pass it — sending
    a day-90 check-in on day 1 would be worse than sending none.
    """
    query = (db.query(CheckIn)
             .join(Patient, CheckIn.patient_id == Patient.id)
             .filter(CheckIn.completed_at.is_(None),
                     Patient.organisation_id == user.organisation_id))
    if not include_scheduled:
        query = query.filter(CheckIn.scheduled_for <= utcnow())
    rows = query.order_by(CheckIn.scheduled_for).all()
    return [CheckInResponse(
        id=c.id, patient_id=c.patient_id,
        patient_ref=c.patient.patient_ref if c.patient else None,
        scheduled_for=c.scheduled_for,
        completed_at=c.completed_at, escalated=c.escalated, responses=c.responses,
    ) for c in rows]


@app.get("/api/checkins/by-token", response_model=CheckInResponse,
         tags=["follow-up"])
def checkin_by_token(token: str, db: Session = Depends(get_db)):
    """The one check-in a carer's link refers to. No session required.

    This is the carer's entire view of the system: one check-in, theirs, and
    nothing once it is answered. `/api/checkins/due` stays clinician-only —
    handing a carer a list of every due check-in in the hospital would be the
    same leak this whole change exists to close.
    """
    c = checkin_by_access_token(token, db)
    return CheckInResponse(
        id=c.id, patient_id=c.patient_id,
        patient_ref=c.patient.patient_ref if c.patient else None,
        scheduled_for=c.scheduled_for,
        completed_at=c.completed_at, escalated=c.escalated, responses=c.responses)


@app.post("/api/checkins/{checkin_id}/respond", tags=["follow-up"])
def submit_checkin(checkin_id: int, sub: CheckInSubmission, request: Request,
                   db: Session = Depends(get_db)):
    """Caregiver response. Escalation rules are deliberately conservative —
    a missed escalation costs more than a false alarm.

    Reachable two ways, both real: a carer holding this check-in's token, or a
    clinician with a session. The carer's token must match THIS check-in, so
    holding one for check-in 4 grants nothing about check-in 5.
    """
    c = caregiver_or_clinician_checkin(checkin_id, request, db)

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
def escalations(db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Clinician inbox.

    Returns the triage record alongside the flag so a clinician can see WHY it
    was raised and by what — the boolean rules or the agent — plus which tools
    the agent consulted. An escalation a clinician cannot audit is one they will
    learn to ignore.
    """
    rows = (db.query(CheckIn)
              .join(Patient, CheckIn.patient_id == Patient.id)
              .filter(CheckIn.escalated.is_(True),
                      Patient.organisation_id == user.organisation_id)
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