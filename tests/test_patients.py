"""
Tests for the patient record endpoints.

These are the first tests in the suite that go through the HTTP layer. Every
other test file exercises a package directly, which meant the two most expensive
bugs of the project — `RetrievedPassage.trigger` rejecting null, and the webhook
timing out — were both found by hand instead of here.

What is actually worth asserting on this endpoint
-------------------------------------------------
Not "does it return 200". The three properties that matter:

  1. `can_send` AGREES WITH THE POLICY GATE. The screen exists partly to tell a
     clinician that follow-up has stopped. If it derives that from
     `consent_recorded` while the sender derives it from `may_send()`, the two
     will diverge and the screen will confidently lie.
  2. `status` DISTINGUISHES `overdue` FROM `sent`. One is our failure, one is the
     carer's silence. Collapsing them hides the only actionable case.
  3. THE FULL PHONE NUMBER IS NEVER RETURNED. This is the screen that gets
     demonstrated on a projector.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient


# `client`, `db` and `org_id` come from conftest.py — a signed-in clinician
# against a fresh schema. Every patient below must belong to that clinician's
# organisation or it is invisible to the scoped queries, which is the correct
# production behaviour and a confusing empty list in a test.


def _patient(db, org_id, **kwargs):
    from api.database import Patient

    defaults = dict(organisation_id=org_id, patient_ref="test-01",
                    caregiver_contact="+919999999999", consent_recorded=True)
    p = Patient(**{**defaults, **kwargs})
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _checkin(db, patient_id, **kwargs):
    from api.database import CheckIn, utcnow

    defaults = dict(patient_id=patient_id, scheduled_for=utcnow(),
                    reason="Day 3 check-in")
    c = CheckIn(**{**defaults, **kwargs})
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# --------------------------------------------------------------------- basics
def test_unknown_patient_is_404(client):
    assert client.get("/api/patients/99999").status_code == 404


def test_detail_returns_declared_shape(client, db, org_id):
    p = _patient(db, org_id)
    body = client.get(f"/api/patients/{p.id}").json()

    # The endpoint returned an undeclared dict before this change, so a missing
    # key was a frontend `undefined` rather than a server error.
    for key in ("id", "patient_ref", "created_at", "messaging", "assessments",
                "check_ins", "latest_tier_summary", "open_escalations",
                "next_check_in"):
        assert key in body, f"missing {key}"


# ------------------------------------------------------- messaging state truth
def test_can_send_agrees_with_the_policy_gate(client, db, org_id):
    """The property that matters: no independent re-derivation.

    Rather than asserting a hardcoded expectation, this calls `may_send()` — the
    gate the sender uses — and requires the endpoint to match it. If someone
    later reimplements the logic in the endpoint, this fails even if both
    versions look reasonable in isolation.
    """
    from messaging import may_send

    for kwargs in (
        dict(consent_recorded=True, opted_out=False),
        dict(consent_recorded=False, opted_out=False),
        dict(consent_recorded=True, opted_out=True),
        dict(consent_recorded=True, opted_out=False, caregiver_contact=None),
        dict(consent_recorded=True, opted_out=False, caregiver_contact="not-a-phone"),
    ):
        p = _patient(db, org_id, patient_ref=None, **kwargs)
        m = client.get(f"/api/patients/{p.id}").json()["messaging"]

        expected = may_send(
            consent_recorded=bool(p.consent_recorded),
            contact=p.caregiver_contact,
            opted_out=bool(p.opted_out),
            last_sent_at=None,
        )
        assert m["can_send"] is bool(expected), kwargs
        if not expected:
            assert m["blocked_reason"] == expected.reason, kwargs


def test_opt_out_is_reported_even_when_consent_is_recorded(client, db, org_id):
    """Opt-out outranks consent. A screen showing "consent: yes" next to a
    silent patient with no explanation is how someone concludes the system is
    broken rather than behaving correctly."""
    p = _patient(db, org_id, consent_recorded=True, opted_out=True)
    m = client.get(f"/api/patients/{p.id}").json()["messaging"]

    assert m["consent_recorded"] is True
    assert m["opted_out"] is True
    assert m["can_send"] is False
    assert "opted out" in m["blocked_reason"].lower()


def test_full_phone_number_is_never_returned(client, db, org_id):
    number = "+919715618753"
    p = _patient(db, org_id, caregiver_contact=number)
    raw = client.get(f"/api/patients/{p.id}").text

    assert number not in raw
    assert number.lstrip("+") not in raw
    m = client.get(f"/api/patients/{p.id}").json()["messaging"]
    assert m["contact_hint"] == "…8753"
    assert m["caregiver_contact_on_file"] is True


def test_missing_contact_reports_absence_rather_than_a_blank(client, db, org_id):
    p = _patient(db, org_id, caregiver_contact=None)
    m = client.get(f"/api/patients/{p.id}").json()["messaging"]

    assert m["caregiver_contact_on_file"] is False
    assert m["contact_hint"] is None


# --------------------------------------------------------- whatsapp 24h window
def test_window_is_closed_when_the_carer_has_never_written(client, db, org_id):
    p = _patient(db, org_id)
    m = client.get(f"/api/patients/{p.id}").json()["messaging"]

    assert m["whatsapp_window_open"] is False
    assert "never" in m["whatsapp_window_note"].lower()


def test_window_open_only_within_24_hours(client, db, org_id):
    from api.database import utcnow

    recent = _patient(db, org_id, last_inbound_at=utcnow() - timedelta(hours=2))
    stale = _patient(db, org_id, last_inbound_at=utcnow() - timedelta(hours=30))

    assert client.get(f"/api/patients/{recent.id}").json()[
        "messaging"]["whatsapp_window_open"] is True
    stale_m = client.get(f"/api/patients/{stale.id}").json()["messaging"]
    assert stale_m["whatsapp_window_open"] is False
    # The error code is named so the reader knows what a failed send looks like.
    assert "21654" in stale_m["whatsapp_window_note"]


# ------------------------------------------------------- check-in status logic
def test_status_separates_our_failure_from_the_carers_silence(client, db, org_id):
    """`overdue` and `sent` are the whole point of this field.

    A check-in past its date with nothing sent means the scheduler is off, or
    consent is missing, or the rate limiter fired — all ours to fix. A check-in
    that was sent and not answered is the carer's. One string for both would
    hide the actionable half.
    """
    from api.database import utcnow

    p = _patient(db, org_id)
    past, future = utcnow() - timedelta(days=2), utcnow() + timedelta(days=5)

    overdue = _checkin(db, p.id, scheduled_for=past)
    sent = _checkin(db, p.id, scheduled_for=past, sent_at=utcnow())
    done = _checkin(db, p.id, scheduled_for=past, sent_at=utcnow(),
                    completed_at=utcnow())
    later = _checkin(db, p.id, scheduled_for=future)

    got = {c["id"]: c["status"]
           for c in client.get(f"/api/patients/{p.id}").json()["check_ins"]}

    assert got[overdue.id] == "overdue"
    assert got[sent.id] == "sent"
    assert got[done.id] == "completed"
    assert got[later.id] == "scheduled"


def test_completed_outranks_overdue(client, db, org_id):
    """A check-in answered late is answered, not overdue."""
    from api.database import utcnow

    p = _patient(db, org_id)
    c = _checkin(db, p.id, scheduled_for=utcnow() - timedelta(days=10),
                 completed_at=utcnow())
    body = client.get(f"/api/patients/{p.id}").json()
    assert body["check_ins"][0]["status"] == "completed"
    assert c.id == body["check_ins"][0]["id"]


def test_check_ins_read_forwards(client, db, org_id):
    from api.database import utcnow

    p = _patient(db, org_id)
    for offset in (10, -5, 3):
        _checkin(db, p.id, scheduled_for=utcnow() + timedelta(days=offset))

    days = [c["scheduled_for"]
            for c in client.get(f"/api/patients/{p.id}").json()["check_ins"]]
    assert days == sorted(days)


def test_next_check_in_skips_past_and_completed(client, db, org_id):
    from api.database import utcnow

    p = _patient(db, org_id)
    _checkin(db, p.id, scheduled_for=utcnow() - timedelta(days=3))
    _checkin(db, p.id, scheduled_for=utcnow() + timedelta(days=2),
             completed_at=utcnow())
    upcoming = _checkin(db, p.id, scheduled_for=utcnow() + timedelta(days=9))

    body = client.get(f"/api/patients/{p.id}").json()
    assert body["next_check_in"] is not None
    assert str(upcoming.scheduled_for.date()) in body["next_check_in"]


# ------------------------------------------------------------------- escalation
def test_triage_record_is_returned_whole(client, db, org_id):
    """Rule reasons and agent reasons stay separate all the way to the client.

    Merging them server-side would be tidier and would destroy the reviewer's
    ability to calibrate trust in the agent separately from the rules.
    """
    from api.database import utcnow

    p = _patient(db, org_id)
    triage = {
        "escalated": True, "escalation_reason": "New confusion reported",
        "rule_reasons": [], "agent_reasons": ["sudden-onset cognitive change"],
        "urgency": "urgent", "agent_summary": "no prior baseline confusion",
        "tool_calls": [{"name": "check_in_history", "arguments": {},
                        "ok": True, "error": None}],
        "mode": "agent", "agent_error": None,
    }
    _checkin(db, p.id, completed_at=utcnow(), escalated=True,
             escalation_reason="New confusion reported", urgency="urgent",
             triage=triage)

    c = client.get(f"/api/patients/{p.id}").json()["check_ins"][0]
    assert c["urgency"] == "urgent"
    assert c["triage"]["rule_reasons"] == []
    assert c["triage"]["agent_reasons"] == ["sudden-onset cognitive change"]
    assert c["triage"]["tool_calls"][0]["name"] == "check_in_history"


def test_open_escalations_counted_on_both_endpoints(client, db, org_id):
    from api.database import utcnow

    p = _patient(db, org_id)
    _checkin(db, p.id, completed_at=utcnow(), escalated=True, urgency="urgent")
    _checkin(db, p.id, completed_at=utcnow(), escalated=True, urgency="soon")
    _checkin(db, p.id, completed_at=utcnow(), escalated=False)

    assert client.get(f"/api/patients/{p.id}").json()["open_escalations"] == 2
    row = next(r for r in client.get("/api/patients").json() if r["id"] == p.id)
    assert row["open_escalations"] == 2


# -------------------------------------------------------------- list endpoint
def test_list_surfaces_the_two_silent_failures(client, db, org_id):
    """A row for a patient nobody is contacting must not look like a healthy one."""
    ok = _patient(db, org_id, patient_ref="ok", consent_recorded=True)
    no_consent = _patient(db, org_id, patient_ref="no-consent", consent_recorded=False)
    gone = _patient(db, org_id, patient_ref="opted-out", opted_out=True)

    rows = {r["patient_ref"]: r for r in client.get("/api/patients").json()}
    assert rows["ok"]["consent_recorded"] is True and rows["ok"]["opted_out"] is False
    assert rows["no-consent"]["consent_recorded"] is False
    assert rows["opted-out"]["opted_out"] is True
    assert {ok.id, no_consent.id, gone.id} <= {r["id"] for r in rows.values()}


def test_patient_with_no_assessments_does_not_break_either_endpoint(client, db, org_id):
    """The list used to index `results["risks"]` unguarded."""
    p = _patient(db, org_id)
    assert client.get(f"/api/patients/{p.id}").json()["latest_tier_summary"] is None
    row = next(r for r in client.get("/api/patients").json() if r["id"] == p.id)
    assert row["latest_tier_summary"] is None
    assert row["assessment_count"] == 0


def test_assessment_inputs_are_returned_for_review(client, db, org_id):
    """A tier with no visible input is not reviewable — a clinician disagreeing
    with it needs to see whether the model or the data is wrong."""
    from api.database import Assessment

    p = _patient(db, org_id)
    db.add(Assessment(
        patient_id=p.id,
        inputs={"age": 74, "sex": "F", "deficit_arm": "present"},
        results={"risks": [{"outcome": "death_14d", "label": "Death",
                            "tier": "moderate", "percentile": 61,
                            "actionability": "actionable"}]},
        guidance_triggers=["mobility_and_falls"]))
    db.commit()

    a = client.get(f"/api/patients/{p.id}").json()["assessments"][0]
    assert a["inputs"]["age"] == 74
    assert a["guidance_triggers"] == ["mobility_and_falls"]
    assert client.get(f"/api/patients/{p.id}").json()[
        "latest_tier_summary"] == {"death_14d": "moderate"}


def test_assessments_are_newest_first(client, db, org_id):
    from api.database import Assessment, utcnow

    p = _patient(db, org_id)
    for days in (0, 5, 2):
        db.add(Assessment(patient_id=p.id,
                          created_at=utcnow() - timedelta(days=days),
                          inputs={}, results={"risks": []},
                          guidance_triggers=[]))
    db.commit()

    dates = [a["created_at"]
             for a in client.get(f"/api/patients/{p.id}").json()["assessments"]]
    assert dates == sorted(dates, reverse=True)


# --------------------------------------------------------------- form schema
# `api/artifacts/schema.json` is written when the models are trained and served
# verbatim by GET /api/meta/schema. `AssessmentRequest` is the live Pydantic
# model. Nothing regenerates one from the other, so they drift silently — and
# the assessment form now fills its selects from the schema, so drift puts an
# option in front of a clinician that the API rejects when they submit.
#
# Asserted against the FILE rather than the endpoint on purpose. The endpoint
# returns {} in tests, because `predictor.load()` runs in a startup hook and
# TestClient only fires those as a context manager. Testing through HTTP would
# have passed vacuously on an empty dict and proved nothing.

import json
import pathlib

SCHEMA_FILE = pathlib.Path(__file__).resolve().parents[1] / "api" / "artifacts" / "schema.json"


def _served_options():
    """{field name: [legal values]} for every select the schema describes."""
    payload = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    return {f["name"]: [o["value"] for o in f["options"]]
            for section in payload["sections"]
            for f in section["fields"] if f.get("options")}


def _model_enums():
    """{field name: [legal values]} from AssessmentRequest itself."""
    from api.schemas import AssessmentRequest

    model = AssessmentRequest.model_json_schema()
    defs = model.get("$defs", {})
    out = {}
    for name, spec in model.get("properties", {}).items():
        for ref in [spec] + spec.get("allOf", []) + spec.get("anyOf", []):
            target = ref.get("$ref", "")
            if target.startswith("#/$defs/"):
                enum = defs.get(target.rsplit("/", 1)[-1], {}).get("enum")
                if enum:
                    out[name] = list(enum)
                    break
            elif "enum" in ref:
                out[name] = list(ref["enum"])
                break
    return out


def test_the_form_never_offers_a_value_the_api_rejects():
    """The dangerous direction of drift.

    The app presents a choice, the clinician picks it, and the submission fails
    with a validation error naming a field they filled in correctly.
    """
    allowed = _model_enums()
    for name, options in _served_options().items():
        assert name in allowed, (
            f"schema.json gives '{name}' a fixed option list, but "
            f"AssessmentRequest does not constrain it")
        extra = sorted(set(options) - set(allowed[name]))
        assert not extra, f"schema.json offers {extra} for '{name}'; the API rejects them"


def test_the_form_can_offer_every_value_the_api_accepts():
    """The quiet direction.

    Nothing breaks — but a value the model gained and the schema never learned
    about is unreachable from the UI, so a clinician cannot record a real
    finding. A silent loss of data rather than a visible error.
    """
    served = _served_options()
    for name, allowed in _model_enums().items():
        if name not in served:
            continue
        missing = sorted(set(allowed) - set(served[name]))
        assert not missing, (
            f"AssessmentRequest accepts {missing} for '{name}', which the form "
            f"has no way to offer")


# ------------------------------------------------------------ clearing opt-out
# The most dangerous route in the app: a clinician deciding a carer's withdrawal
# did not count. It exists because `opted_out` is set by any inbound message
# matching STOP, so a mistyped reply removed a family from follow-up forever.
# These tests pin the three things that make it defensible rather than a hole.


def test_clearing_an_opt_out_requires_a_real_reason(client, db, org_id):
    p = _patient(db, org_id, opted_out=True, consent_recorded=True,
                 caregiver_contact="+919876543210")

    for bad in ("", "   ", "ok", "mistake"):
        r = client.post(f"/api/patients/{p.id}/opt-out/clear",
                        json={"reason": bad})
        assert r.status_code == 400, f"accepted {bad!r} as a justification"

    db.refresh(p)
    assert p.opted_out is True, "a rejected request must not clear the flag"


def test_clearing_records_who_did_it_and_why(client, db, org_id):
    p = _patient(db, org_id, opted_out=True, consent_recorded=True,
                 caregiver_contact="+919876543210")

    reason = "Carer confirmed by phone that STOP was sent by mistake."
    body = client.post(f"/api/patients/{p.id}/opt-out/clear",
                       json={"reason": reason}).json()

    db.refresh(p)
    assert p.opted_out is False
    assert p.opt_out_cleared_reason == reason
    assert p.opt_out_cleared_by, "the clinician's identity was not recorded"
    assert p.opt_out_cleared_at is not None
    assert body["opted_out"] is False
    assert body["opt_out_cleared_reason"] == reason


def test_the_withdrawal_stays_on_the_record_after_it_is_cleared(client, db, org_id):
    """The point of the whole design.

    After a clear the patient must never read as someone who simply never
    objected. `opted_out_at` is the date of the withdrawal and it outlives the
    withdrawal — a screen showing `opted_out: false` with `opted_out_at` set is
    showing an override, and that distinction is the audit trail.
    """
    from api.database import utcnow

    p = _patient(db, org_id, opted_out=True, opted_out_at=utcnow(),
                 consent_recorded=True, caregiver_contact="+919876543210")

    client.post(f"/api/patients/{p.id}/opt-out/clear",
                json={"reason": "Confirmed with the family that this was an error."})

    m = client.get(f"/api/patients/{p.id}").json()["messaging"]
    assert m["opted_out"] is False
    assert m["opted_out_at"] is not None, "the withdrawal was erased from the record"
    assert m["opt_out_cleared_at"] is not None


def test_clearing_does_not_manufacture_consent(client, db, org_id):
    """Restoring the previous state is not the same as a fresh agreement.

    A patient who opted out and never had consent recorded must still be
    unsendable afterwards. Otherwise this route becomes a way to acquire consent
    by typing a sentence.
    """
    p = _patient(db, org_id, opted_out=True, consent_recorded=False,
                 caregiver_contact="+919876543210")

    body = client.post(f"/api/patients/{p.id}/opt-out/clear",
                       json={"reason": "Carer says the STOP was sent in error."}).json()

    assert body["opted_out"] is False
    assert body["can_send"] is False, "clearing an opt-out granted consent"


def test_cannot_clear_an_opt_out_that_never_happened(client, db, org_id):
    p = _patient(db, org_id, opted_out=False)
    r = client.post(f"/api/patients/{p.id}/opt-out/clear",
                    json={"reason": "There is nothing here to clear at all."})
    assert r.status_code == 400
