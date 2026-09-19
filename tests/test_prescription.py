"""
Tests for prescription capture.

WHAT IS WORTH ASSERTING HERE
----------------------------
Not "does it find three medicines". The properties that matter are the ones
that stop a wrong dose reaching a family:

  1. A DOSE LANDS IN THE RIGHT COLUMN. Flattening the PDF to text destroys
     this — "1 1 3" is equally consistent with morning/noon/days and
     morning/night/days — so the parse is geometric and these tests pin the
     geometry.
  2. AN UNREADABLE CELL IS REFUSED, NOT GUESSED. A blank field costs typing.
     A confident wrong dose does not get noticed.
  3. NOTHING SCHEDULES A REMINDER IT CANNOT SCHEDULE. 'As needed' has no daily
     time; a row with no course length has no end date.
  4. NO IDENTIFIER IS EVER PARSED. The source layout carries a name, hospital
     number and address. None of them may reach the record.
  5. NOTHING REACHES A CARER UNCONFIRMED.
"""

import pathlib

import pytest

from prescription import parse_pdf, parse_words

TESTDATA = pathlib.Path(__file__).resolve().parents[1] / "prescription" / "testdata"


def _pdf(name: str):
    path = TESTDATA / name
    if not path.exists():
        pytest.skip(f"{name} not generated — run python -m prescription.testdata.make_synthetic")
    return parse_pdf(path.read_bytes())


# ------------------------------------------------------------ the geometry
def test_a_dose_is_assigned_to_the_column_it_sits_under():
    """The core claim of the whole design.

    CEFVIL prints a 1 under Morning and a 1 under Night, with Noon and Evening
    empty. Text extraction yields "1 1 3" and loses which is which. Reading the
    x-coordinates does not.
    """
    r = _pdf("prescription_basic.pdf")
    cefvil = next(m for m in r.medications if m.name.startswith("CEFVIL"))

    assert cefvil.morning == "1"
    assert cefvil.night == "1"
    assert cefvil.noon is None, "invented a midday dose from an empty cell"
    assert cefvil.evening is None
    assert cefvil.days == 3


def test_a_midday_dose_is_not_confused_with_a_night_dose():
    """The inverse case, which is the one that would poison the whole idea.

    AUGMENTIN is morning/noon/night and SHELCAL is noon only. If columns were
    being assigned by order-of-appearance rather than position, SHELCAL's single
    dose would land in Morning.
    """
    r = _pdf("prescription_three_times.pdf")
    shelcal = next(m for m in r.medications if m.name.startswith("SHELCAL"))

    assert shelcal.noon == "1"
    assert shelcal.morning is None, "a noon-only dose was read as a morning dose"
    assert shelcal.night is None

    augmentin = next(m for m in r.medications if m.name.startswith("AUGMENTIN"))
    assert (augmentin.morning, augmentin.noon, augmentin.night) == ("1", "1", "1")
    assert augmentin.evening is None


def test_a_long_medicine_name_does_not_spill_into_the_quantity():
    """Found by the fixtures: splitting columns at the midpoint between headers
    put the tail of "PARACETAMOL 650 TAB" into Qty, which came back as "TAB 10".
    Columns run from their own anchor to the next one."""
    r = _pdf("prescription_sparse.pdf")
    med = r.medications[0]

    assert med.name == "PARACETAMOL 650 TAB"
    assert med.qty == "10"


def test_half_doses_survive():
    """0.5 is a real instruction, not a parse failure, and must not be rounded."""
    r = _pdf("prescription_awkward.pdf")
    med = next(m for m in r.medications if m.name.startswith("DEFLAZACORT"))

    assert med.morning == "0.5"
    assert med.night == "0.5"


# --------------------------------------------------------------- refusing
def test_an_unreadable_dose_is_rejected_rather_than_guessed():
    words = [
        {"text": "S.No.", "x0": 50, "top": 100},
        {"text": "Medicine", "x0": 90, "top": 100},
        {"text": "Morning", "x0": 300, "top": 100},
        {"text": "Night", "x0": 400, "top": 100},
        {"text": "Days", "x0": 500, "top": 100},
        {"text": "1", "x0": 50, "top": 120},
        {"text": "SOMEDRUG", "x0": 90, "top": 120},
        {"text": "1-0-1", "x0": 300, "top": 120},   # a schedule crammed into one cell
        {"text": "3", "x0": 500, "top": 120},
    ]
    r = parse_words(words)

    med = r.medications[0]
    assert med.morning is None, "'1-0-1' was accepted as a morning dose"
    assert any(x["field"] == "morning" and x["value"] == "1-0-1" for x in r.rejected)


def test_an_implausible_course_length_is_rejected():
    words = [
        {"text": "S.No.", "x0": 50, "top": 100},
        {"text": "Medicine", "x0": 90, "top": 100},
        {"text": "Morning", "x0": 300, "top": 100},
        {"text": "Night", "x0": 400, "top": 100},
        {"text": "Days", "x0": 500, "top": 100},
        {"text": "1", "x0": 50, "top": 120},
        {"text": "SOMEDRUG", "x0": 90, "top": 120},
        {"text": "1", "x0": 300, "top": 120},
        {"text": "999", "x0": 500, "top": 120},
    ]
    r = parse_words(words)

    assert r.medications[0].days is None
    assert any(x["field"] == "days" for x in r.rejected)
    assert not r.medications[0].schedulable


def test_the_footer_is_not_read_as_a_medicine():
    """Without a terminator, "Timing : 9am to 6pm" becomes a drug."""
    r = _pdf("prescription_basic.pdf")
    names = " ".join(m.name.lower() for m in r.medications)

    assert "timing" not in names
    assert "synthetic" not in names
    assert len(r.medications) == 3


# ------------------------------------------------------------- scheduling
def test_an_as_needed_medicine_is_never_scheduled():
    """A painkiller marked SOS has no daily time to ask about. Asking 'did you
    take it yesterday' about a drug meant for pain would read as an instruction
    to take it."""
    r = _pdf("prescription_awkward.pdf")
    dolo = next(m for m in r.medications if m.name.startswith("DOLO"))

    assert dolo.as_needed is True
    assert dolo.schedulable is False
    assert dolo in r.medications, "it must still appear on the clinician's list"


def test_a_row_without_a_course_length_is_not_schedulable():
    words = [
        {"text": "S.No.", "x0": 50, "top": 100},
        {"text": "Medicine", "x0": 90, "top": 100},
        {"text": "Morning", "x0": 300, "top": 100},
        {"text": "Night", "x0": 400, "top": 100},
        {"text": "Days", "x0": 500, "top": 100},
        {"text": "1", "x0": 50, "top": 120},
        {"text": "SOMEDRUG", "x0": 90, "top": 120},
        {"text": "1", "x0": 300, "top": 120},
    ]
    r = parse_words(words)

    assert r.medications[0].schedulable is False
    assert any("no course length" in w for w in r.warnings)


# ---------------------------------------------------------------- privacy
def test_no_identifier_is_ever_extracted():
    """The layout this parses carries a name, hospital number, address and
    prescription number. The parser has no field for any of them, and this test
    exists so that stays true when someone adds fields later."""
    r = _pdf("prescription_basic.pdf")

    blob = str(r.to_json()).lower()
    for identifier in ("sample", "syn0001", "q000101", "synthetic city", "uhid"):
        assert identifier not in blob, f"{identifier!r} reached the parsed record"

    assert not hasattr(r, "patient_name")
    assert not any(hasattr(m, "patient_name") for m in r.medications)


def test_the_diagnosis_is_read_but_is_not_an_identifier():
    """Clinical context for the clinician's own screen. It is never sent to a
    carer — see _compose_medication_message, which reads only the medicine list."""
    r = _pdf("prescription_basic.pdf")
    assert r.diagnosis == "WART LEFT FOOT"


# -------------------------------------------------------- the API contract
def test_parsing_creates_nothing(client, db, org_id):
    """The parse endpoint is a read. A prescription exists only once a clinician
    has confirmed it."""
    from api.database import Prescription

    before = db.query(Prescription).count()
    pdf = (TESTDATA / "prescription_basic.pdf")
    if not pdf.exists():
        pytest.skip("fixtures not generated")

    r = client.post("/api/prescriptions/parse", content=pdf.read_bytes(),
                    headers={"Content-Type": "application/pdf"})

    assert r.status_code == 200
    assert len(r.json()["medications"]) == 3
    assert db.query(Prescription).count() == before


def test_confirming_schedules_only_the_schedulable_rows(client, db, org_id):
    from api.database import CheckIn, Patient

    p = Patient(organisation_id=org_id, patient_ref="WARD-01")
    db.add(p)
    db.commit()

    body = {
        "medications": [
            {"name": "CEFVIL 200 TAB", "morning": "1", "night": "1", "days": 3},
            {"name": "DOLO 650 TAB", "take": "SOS", "as_needed": True, "days": 3},
            {"name": "NO SCHEDULE TAB", "days": 3},
        ],
        "source": "pdf_geometry",
    }
    out = client.post(f"/api/patients/{p.id}/prescription", json=body).json()

    assert out["course_days"] == 3
    assert out["scheduled_checkins"] == 3
    assert set(out["not_scheduled"]) == {"DOLO 650 TAB", "NO SCHEDULE TAB"}
    assert out["confirmed_by"], "the confirming clinician was not recorded"

    rounds = db.query(CheckIn).filter(CheckIn.patient_id == p.id,
                                      CheckIn.kind == "medication").all()
    assert len(rounds) == 3
    assert all(c.prescription_id for c in rounds)


def test_a_prescription_cannot_be_confirmed_for_another_organisation(client, db):
    """Same 404-not-403 rule as every other patient route: a stranger's patient
    id must not be confirmable, and must not reveal that it exists."""
    from api.database import Organisation, Patient

    other = Organisation(name="Someone Else")
    db.add(other)
    db.commit()
    victim = Patient(organisation_id=other.id, patient_ref="THEIRS")
    db.add(victim)
    db.commit()

    r = client.post(f"/api/patients/{victim.id}/prescription", json={
        "medications": [{"name": "X TAB", "morning": "1", "days": 3}]})

    assert r.status_code == 404


def test_the_carer_message_lists_medicines_and_gives_no_dosing_advice():
    """The message names what to check and asks a question. It must never tell
    anyone to take, skip, double or stop a dose — a missed-dose rule that reads
    as helpful is wrong for several drugs on a stroke discharge sheet."""
    from messaging import compose_medication_checkin

    body, _ = compose_medication_checkin(
        2, 3, [{"name": "CEFVIL 200 TAB", "schedule_text": "1 morning, 1 night"}],
        patient_ref="WARD-01", language="en")

    assert "CEFVIL 200 TAB" in body
    assert "1 morning, 1 night" in body
    for forbidden in ("take it now", "double", "skip", "stop taking",
                      "you should take", "increase", "reduce the dose"):
        assert forbidden not in body.lower(), f"dosing advice leaked: {forbidden!r}"
    assert "STOP" in body, "the opt-out instruction is missing"
