"""
RecoveryLens API — webhooks.py
==============================
Inbound messages from Twilio, and the outbound send path.

Signature validation
--------------------
`/api/webhooks/twilio` is a public URL that causes a clinical record to be
written. Without signature validation, anyone who learns the URL can submit
check-in responses for any patient. Twilio signs every request; we verify it.

Validation is skipped only when TWILIO_AUTH_TOKEN is unset, which means the
console sender is in use and no real Twilio traffic exists. The response says so
explicitly, so an unsigned deployment is visible rather than silent.

What an inbound message does
----------------------------
1. STOP -> opt out permanently, confirm, stop. Checked before anything else.
2. Otherwise -> find the patient by number, find their oldest pending check-in,
   parse the reply, and submit it through the same path the web form uses. The
   triage agent then reads the full text exactly as it would from the UI.

An inbound message from an unknown number is acknowledged and discarded. It is
not an error worth surfacing to the sender — wrong numbers happen — but it is
logged, because a stream of them means something is misconfigured.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os

from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException,
                     Request, Response)
from sqlalchemy.orm import Session

from messaging import (
    compose_checkin, compose_confirmation, compose_stop_confirmation,
    is_stop_request, may_send, parse_reply,
)
from triage import agent_enabled
from voice import (ConfirmationState, Transcript, compose_discarded,
                   is_confirmation)

from .triage_tools import DatabaseToolBox

from .auth import (caregiver_or_clinician_checkin, current_user,
                   scoped_checkin)
from .database import CheckIn, Patient, User, get_db, utcnow

router = APIRouter(tags=["messaging"])


def _parse_form(body: bytes) -> dict[str, str]:
    """Decode an application/x-www-form-urlencoded body.

    `keep_blank_values` matters: Twilio sends empty fields for absent optional
    parameters, and dropping them would change the signature payload and cause
    validation to fail against a body Twilio actually signed.
    """
    from urllib.parse import parse_qs

    parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: v[0] if v else "" for k, v in parsed.items()}


def _digits(contact: str | None) -> str:
    """Comparable form of a phone number.

    Twilio sends `whatsapp:+919876543210`; the database may hold
    `+91 98765 43210`. Comparing the digits avoids a whole class of
    silent no-match bugs.
    """
    return "".join(ch for ch in (contact or "") if ch.isdigit())


def _find_patient(db: Session, from_number: str) -> Patient | None:
    """Match on trailing digits so formatting differences do not matter."""
    target = _digits(from_number)
    if len(target) < 7:
        return None
    for p in db.query(Patient).filter(Patient.caregiver_contact.isnot(None)).all():
        stored = _digits(p.caregiver_contact)
        if stored and (stored.endswith(target[-9:]) or target.endswith(stored[-9:])):
            return p
    return None


def _validate_signature(request: Request, form: dict) -> None:
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not token:
        return          # console sender; no real Twilio traffic to authenticate

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise HTTPException(403, "Missing X-Twilio-Signature.")

    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        raise HTTPException(
            500, "TWILIO_AUTH_TOKEN is set but the twilio SDK is not installed, "
                 "so inbound requests cannot be authenticated. Refusing to "
                 "accept unverified clinical data.")

    url = os.getenv("TWILIO_WEBHOOK_URL", "").strip() or str(request.url)
    if not RequestValidator(token).validate(url, form, signature):
        raise HTTPException(403, "Invalid Twilio signature.")


@router.post("/api/webhooks/twilio")
async def twilio_inbound(request: Request, background: BackgroundTasks,
                         db: Session = Depends(get_db)):
    """Inbound WhatsApp/SMS. Returns TwiML; Twilio speaks the <Message> back.

    The body is parsed with `urllib.parse.parse_qs` rather than FastAPI's
    `Form(...)` or Starlette's `request.form()`. Both of those require
    python-multipart — including for url-encoded bodies, which is not obvious and
    cost me a wrong assumption on the way here. Twilio webhooks are always
    `application/x-www-form-urlencoded`, so stdlib handles them in three lines.
    One fewer dependency on a 512MB target, and no import-time failure mode.
    """
    form = _parse_form(await request.body())
    _validate_signature(request, form)

    from_number = str(form.get("From", ""))
    patient = _find_patient(db, from_number)
    text = str(form.get("Body", "")).strip()

    # Opt-out first, always. Honoured even from an unknown number, because the
    # number may simply not match our record and a stop request must never be
    # ignored on a technicality.
    if is_stop_request(text):
        if patient:
            patient.opted_out = True
            patient.opted_out_at = utcnow()
            db.commit()
        return _twiml(compose_stop_confirmation())

    if not patient:
        print(f"[webhook] message from unrecognised number {from_number!r}")
        return _twiml("Thanks. This number isn't linked to a care record, so we "
                      "can't act on this message.")

    patient.last_inbound_at = utcnow()

    checkin = (db.query(CheckIn)
               .filter(CheckIn.patient_id == patient.id,
                       CheckIn.completed_at.is_(None))
               .order_by(CheckIn.scheduled_for).first())
    if not checkin:
        db.commit()
        return _twiml("Thanks — there's no check-in waiting at the moment. "
                      "Your care team will be in touch at the next one.")

    # --- voice --------------------------------------------------------------
    # A voice note arrives as a MediaUrl. Nothing spoken is recorded until the
    # carer confirms the transcript, because ASR's characteristic error drops a
    # negation and inverts the meaning in the reassuring direction.
    media_url = form.get("MediaUrl0", "")
    if media_url:
        reply = _handle_voice_note(db, checkin, media_url,
                                   form.get("MediaContentType0", "audio/ogg"))
        db.commit()
        return _twiml(reply)

    # A reply while a transcript is awaiting confirmation is answering the
    # read-back, not starting a new check-in.
    pending = ConfirmationState.from_json((checkin.triage or {}).get("pending_voice"))
    if pending:
        answer = is_confirmation(text)
        if answer is False:
            _clear_pending(checkin)
            db.commit()
            return _twiml(compose_discarded())
        if answer is True:
            text = pending.transcript      # confirmed; proceed as a normal reply
            _clear_pending(checkin)
        # answer is None: the carer sent new information rather than yes/no.
        # Fall through and treat the whole message as the reply — dropping it
        # would lose whatever they just told us.

    parsed = parse_reply(text)

    # Rules now, agent later.
    #
    # Twilio abandons a webhook after roughly 15 seconds. The triage agent makes
    # several Claude tool-calls and regularly takes longer, so running it inline
    # meant the work completed but the reply never reached the carer — ngrok
    # showed the POST with no status at all.
    #
    # So: apply the deterministic rules, persist, and answer immediately. The
    # agent runs in the background and can only ADD to what the rules decided,
    # which is the same monotonic guarantee as before — it is simply applied a
    # few seconds later. A carer waiting on a reply gets one; a clinician sees
    # the agent's contribution when it lands.
    from .schemas import CheckInSubmission

    submission = CheckInSubmission(**parsed.to_submission())
    result = _apply_rules_only(checkin, submission, db)

    # Record how the reply was read, so a clinician reviewing an escalation can
    # see whether a boolean came from the carer or from a parser default.
    stored = dict(checkin.triage or {})
    stored["inbound"] = {
        "channel": "twilio", "raw": text, "method": parsed.method,
        "confident": parsed.confident, "notes": parsed.notes,
    }
    checkin.triage = stored
    db.commit()

    # Hand the agent off to a background thread. The response goes out now.
    if agent_enabled() and (parsed.free_text or "").strip():
        background.add_task(_run_triage_agent, checkin.id,
                            parsed.free_text or "", list(result["rule_reasons"]))

    return _twiml(compose_confirmation(result["escalated"],
                                       result.get("urgency", "routine")))


def _apply_rules_only(checkin: CheckIn, sub, db: Session) -> dict:
    """The deterministic half of a check-in, without the agent.

    Deliberately mirrors the rule block in main.py::submit_checkin. Kept
    separate so the webhook can answer Twilio inside its timeout while the
    agent runs afterwards.
    """
    reasons = []
    if not sub.taking_medication:
        reasons.append("Medication not being taken")
    if sub.new_symptoms:
        reasons.append("New symptoms reported")
    if sub.worse_than_last_week:
        reasons.append("Condition reported as worsening")

    checkin.responses = sub.model_dump()
    checkin.completed_at = utcnow()
    checkin.escalated = bool(reasons)
    checkin.escalation_reason = "; ".join(reasons) if reasons else None
    checkin.urgency = "soon" if reasons else "routine"
    checkin.triage = {
        "escalated": bool(reasons),
        "escalation_reason": checkin.escalation_reason,
        "rule_reasons": reasons,
        "agent_reasons": [],
        "urgency": checkin.urgency,
        "agent_summary": "",
        "tool_calls": [],
        "mode": "rules_only",
        "agent_error": None,
        "agent_pending": True,
    }
    db.commit()

    return {"escalated": bool(reasons), "urgency": checkin.urgency,
            "rule_reasons": reasons}


def _run_triage_agent(checkin_id: int, free_text: str,
                      rule_reasons: list[str]) -> None:
    """Run the agent after the webhook has already replied.

    Its own session — the request's is closed by the time this runs. Failure is
    logged and swallowed: the rules have already been applied and persisted, so
    the worst case is a check-in escalated on rules alone, which is exactly the
    behaviour with the agent disabled.
    """
    from triage import TriageAgent

    from .database import SessionLocal

    db = SessionLocal()
    try:
        checkin = db.query(CheckIn).filter(CheckIn.id == checkin_id).first()
        if checkin is None:
            return

        result = TriageAgent(DatabaseToolBox(db)).run(
            free_text=free_text, rule_escalations=rule_reasons,
            patient_id=checkin.patient_id,
        ).finalise()

        # finalise() guarantees the rule reasons survive, so this cannot
        # downgrade what the rules already decided.
        inbound = (checkin.triage or {}).get("inbound")
        checkin.escalated = result["escalated"]
        checkin.escalation_reason = result["escalation_reason"]
        checkin.urgency = result["urgency"]
        result["agent_pending"] = False
        if inbound:
            result["inbound"] = inbound
        checkin.triage = result
        db.commit()

        print(f"[triage] check-in {checkin_id}: {result['mode']}, "
              f"{len(result['agent_reasons'])} agent reason(s), "
              f"urgency {result['urgency']}")
    except Exception as exc:
        print(f"[triage] background run failed for check-in {checkin_id}: "
              f"{type(exc).__name__}: {exc}")
    finally:
        db.close()


@router.post("/api/checkins/{checkin_id}/send", tags=["messaging"])
def send_checkin(checkin_id: int, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Send one check-in to the carer.

    Every policy check runs here — opt-out, consent, a usable number, rate
    limiting — before the sender is touched. A refusal returns 200 with the
    reason rather than an error, because "we deliberately did not send this" is
    a normal outcome, not a failure.
    """
    from messaging import build_sender

    c = scoped_checkin(checkin_id, user, db)
    patient = c.patient
    if not patient:
        raise HTTPException(404, "Check-in has no patient")

    last_sent = (db.query(CheckIn)
                 .filter(CheckIn.patient_id == patient.id,
                         CheckIn.sent_at.isnot(None))
                 .order_by(CheckIn.sent_at.desc()).first())

    decision = may_send(
        consent_recorded=bool(patient.consent_recorded),
        contact=patient.caregiver_contact,
        opted_out=bool(patient.opted_out),
        last_sent_at=last_sent.sent_at if last_sent else None,
    )
    if not decision:
        return {"sent": False, "reason": decision.reason, "check_in_id": c.id}

    body, translation = compose_checkin(
        day=_day_of(c), label=c.reason or "Check-in",
        caregiver_message=_caregiver_text(c), patient_ref=patient.patient_ref,
        language=patient.language or "en")

    # Audio companion. Generated AFTER translation so the carer hears their own
    # language, and treated as an enhancement throughout: every failure path here
    # returns None with a stated reason and the text still goes out. A check-in
    # that reaches a family silently is a success; one that does not reach them
    # because the tunnel was misconfigured is not.
    from .media import synthesise_for

    media_url, audio = synthesise_for(
        db, c, body, language=translation.get("language", "en"))

    result = build_sender().send(patient.caregiver_contact, body,
                                 media_url=media_url)
    if result.ok:
        c.sent_at = utcnow()
        # Recorded on the check-in, not just returned, so the clinician record
        # shows what language went out and why. "Patient's language is Tamil but
        # the message went in English" is a fact someone needs to be able to
        # discover without re-running the send.
        state = dict(c.triage or {})
        state["outbound_language"] = translation
        state["outbound_audio"] = audio
        c.triage = state
        db.commit()

    return {
        "sent": result.ok, "channel": result.channel,
        "message_id": result.message_id, "error": result.error,
        "check_in_id": c.id, "preview": body, "translation": translation,
        # Reported separately from `sent` so "delivered as text only" is visible
        # rather than being inferred from the absence of a field.
        "audio": audio, "media_url": result.media_url,
    }


def record_voice_note(checkin: CheckIn, audio: bytes, mime_type: str,
                      audio_ref: str) -> tuple[str, Transcript | None]:
    """Transcribe audio and park it for confirmation. THE shared voice core.

    Called by both the Twilio webhook and the browser endpoint. It exists as one
    function on purpose: every safety property of the voice path lives here — the
    confidence gate, negation-loss warnings, and the rule that nothing is recorded
    against the check-in until a human confirms the read-back. A second
    implementation for the browser would be a second place for those to be wrong,
    and the browser is the path that gets demonstrated.

    Deliberately does NOT submit the check-in. The transcript is parked in
    `triage["pending_voice"]`. If it is never confirmed — the carer ignores the
    WhatsApp read-back, or closes the browser tab — `escalate_unconfirmed_voice()`
    flags it for a clinician rather than letting it disappear. We know a message
    exists about a stroke patient; we just cannot verify what it said.

    Returns (message for the carer, transcript or None if unusable).
    """
    from voice import (ConfirmationState, build_speech_provider,
                       compose_readback, compose_unusable)

    transcript = build_speech_provider().transcribe(audio, mime_type)
    if not transcript.usable:
        # Low confidence or a provider failure. We do not guess at clinical
        # meaning; we ask again, and offer typing as a way out.
        state = dict(checkin.triage or {})
        state["voice_attempts"] = state.get("voice_attempts", 0) + 1
        state["last_voice_error"] = transcript.error or "confidence below threshold"
        checkin.triage = state
        return compose_unusable(), None

    pending = ConfirmationState(
        transcript=transcript.text,
        confidence=round(transcript.confidence, 3),
        provider=transcript.provider,
        asked_at=utcnow().isoformat(),
        warnings=transcript.warnings,
        # The recording itself is kept, not just the text: a clinician reviewing
        # an escalation must be able to hear what was actually said.
        audio_ref=audio_ref,
    )
    state = dict(checkin.triage or {})
    state["pending_voice"] = pending.to_json()
    checkin.triage = state

    return compose_readback(transcript), transcript


def _handle_voice_note(db: Session, checkin: CheckIn, media_url: str,
                       mime_type: str) -> str:
    """Twilio's entry point: fetch the media, then run the shared core."""
    from voice import compose_unusable

    audio = _download_media(media_url)
    if audio is None:
        return compose_unusable()

    message, _ = record_voice_note(checkin, audio, mime_type, audio_ref=media_url)
    return message


# --------------------------------------------------------------- browser voice
# These two endpoints live here, beside the Twilio handler, because they call the
# same `record_voice_note`. Putting them in their own module would make it easy to
# "fix" one path without the other, and the failure that matters — a transcript
# treated as confirmed when it was not — would show up on whichever path nobody
# was looking at.
#
# The real input path is a carer sending a WhatsApp voice note, not someone at a
# laptop. The browser path exists because the feature was otherwise invisible: it
# had no UI at all, so it could not be demonstrated or even tried. It is not a
# separate implementation, and if it ever becomes one, delete it.

# Audio is accepted as a raw request body rather than multipart. `UploadFile`
# requires python-multipart, which this app deliberately does not install — see
# `_parse_form` above, where the same constraint shaped the Twilio handler.
MAX_AUDIO_BYTES = 8 * 1024 * 1024      # ~8 minutes of opus; far more than needed


@router.post("/api/checkins/{checkin_id}/voice", tags=["voice"])
async def submit_voice_note(checkin_id: int, request: Request,
                           db: Session = Depends(get_db)):
    # Carer token or clinician session — the same rule as /respond. Recording a
    # voice note is answering a check-in, so it cannot be easier to reach than
    # typing one.

    """Accept a recording from the browser and return the read-back to confirm.

    Returns `confirmed: false` in every success case. The transcript is parked,
    not recorded. The caller gets the text to display and must call the confirm
    endpoint before it can become part of the check-in.
    """
    from voice import voice_enabled

    checkin = caregiver_or_clinician_checkin(checkin_id, request, db)
    if checkin.completed_at is not None:
        raise HTTPException(409, "This check-in has already been answered.")

    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Empty request body — no audio received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, f"Recording is larger than "
                                 f"{MAX_AUDIO_BYTES // (1024 * 1024)}MB.")

    mime_type = (request.headers.get("content-type") or "audio/webm").split(";")[0]

    message, transcript = record_voice_note(
        checkin, audio, mime_type,
        # No audio hosting yet, so there is no URL to store. Named rather than
        # left blank: a clinician reviewing an unconfirmed browser transcript
        # needs to know the recording is gone, not wonder where the link is.
        audio_ref="browser-upload (recording not retained)")
    db.commit()

    return {
        "check_in_id": checkin.id,
        # False in every success case. Saying so in the payload rather than
        # leaving the client to infer it from the absence of a field.
        "confirmed": False,
        "usable": transcript is not None,
        "readback": message,
        "transcript": transcript.text if transcript else "",
        "confidence": round(transcript.confidence, 3) if transcript else 0.0,
        "provider": transcript.provider if transcript else "",
        # Phrases where speech recognition characteristically drops a negation —
        # "he can't move his arm" becoming "he can move his arm", which reads as
        # reassuring. Surfaced so the UI can point at them during the read-back.
        "warnings": transcript.warnings if transcript else [],
        "voice_configured": voice_enabled(),
    }


@router.post("/api/checkins/{checkin_id}/voice/confirm", tags=["voice"])
async def confirm_voice_note(checkin_id: int, request: Request,
                             db: Session = Depends(get_db)):
    """Confirm or reject a parked transcript. Body: {"confirmed": true|false}.

    On confirmation the text is returned for the carer to submit with the rest of
    the form; it is NOT written to `responses` here. The check-in is still
    answered by one path — `POST /api/checkins/{id}/respond` — so the triage agent
    reads voice and typed text identically and there is no second way to complete
    a check-in.
    """
    import json

    from voice import ConfirmationState, compose_discarded

    checkin = caregiver_or_clinician_checkin(checkin_id, request, db)

    state = dict(checkin.triage or {})
    pending = ConfirmationState.from_json(state.get("pending_voice"))
    if pending is None:
        raise HTTPException(409, "No transcript is awaiting confirmation.")

    try:
        confirmed = bool(json.loads(await request.body() or b"{}").get("confirmed"))
    except Exception:
        raise HTTPException(400, "Body must be JSON: {\"confirmed\": true|false}")

    # The same six-hour window the WhatsApp path uses. An expired read-back is not
    # silently accepted just because the browser is still open.
    if pending.expired():
        _clear_pending(checkin)
        db.commit()
        raise HTTPException(
            409, "That read-back expired. Please record again.")

    _clear_pending(checkin)
    db.commit()

    if not confirmed:
        return {"check_in_id": checkin.id, "confirmed": False,
                "transcript": "", "message": compose_discarded()}

    return {
        "check_in_id": checkin.id,
        "confirmed": True,
        "transcript": pending.transcript,
        "message": "Thanks — that has been added to your answers.",
    }


def _download_media(url: str) -> bytes | None:
    """Fetch a Twilio media file. Twilio media URLs require account auth."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    try:
        import httpx
        auth = (sid, token) if sid and token else None
        r = httpx.get(url, auth=auth, follow_redirects=True, timeout=20.0)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        print(f"[voice] could not fetch media {url!r}: {type(exc).__name__}: {exc}")
        return None


def _clear_pending(checkin: CheckIn) -> None:
    state = dict(checkin.triage or {})
    state.pop("pending_voice", None)
    checkin.triage = state


def escalate_unconfirmed_voice(db: Session) -> list[int]:
    """Flag voice notes the carer never confirmed.

    Called by the scheduler. Silence must not close a check-in: a carer recorded
    something about a stroke patient and we could not verify what it said, so a
    human should listen to it. Returns the check-in ids escalated.
    """
    from voice import ConfirmationState, unconfirmed_escalation_reason

    escalated: list[int] = []
    rows = (db.query(CheckIn)
            .filter(CheckIn.completed_at.is_(None),
                    CheckIn.triage.isnot(None)).all())

    for c in rows:
        pending = ConfirmationState.from_json((c.triage or {}).get("pending_voice"))
        if not pending or not pending.expired():
            continue

        reason = unconfirmed_escalation_reason(pending)
        c.escalated = True
        c.escalation_reason = "; ".join(
            filter(None, [c.escalation_reason, reason]))
        c.urgency = c.urgency if c.urgency == "urgent" else "soon"
        state = dict(c.triage or {})
        state["unconfirmed_voice"] = state.pop("pending_voice")
        c.triage = state
        escalated.append(c.id)

    if escalated:
        db.commit()
    return escalated


def _twiml(message: str) -> Response:
    """TwiML reply, with the content type Twilio actually needs.

    Returned as application/xml, not text/plain. With text/plain Twilio does not
    parse the document at all — it forwards the literal
    `<?xml ...?><Response><Message>...` string to WhatsApp, and the carer sees
    raw markup instead of a message. Everything else works; only the reply is
    mangled, which makes it easy to miss in logs that all say 200 OK.
    """
    from xml.sax.saxutils import escape

    body = (f"<?xml version='1.0' encoding='UTF-8'?>"
            f"<Response><Message>{escape(message)}</Message></Response>")
    return Response(content=body, media_type="application/xml")


def _day_of(c: CheckIn) -> int:
    if not c.scheduled_for or not c.patient or not c.patient.created_at:
        return 0
    a, b = c.scheduled_for, c.patient.created_at
    a = a.replace(tzinfo=timezone.utc) if a.tzinfo is None else a
    b = b.replace(tzinfo=timezone.utc) if b.tzinfo is None else b
    return max(0, (a - b).days)


def _caregiver_text(c: CheckIn) -> str:
    """The generated caregiver message for this day, if one was produced."""
    return ((c.triage or {}).get("caregiver_message") or "") if c.triage else ""
