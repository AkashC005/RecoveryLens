"""
Reading a discharge summary into the assessment form.

What this file defends
----------------------
Autofill is the first feature where a model writes into a CLINICAL INPUT rather
than a display surface. Everything downstream — the six risk models, the tier,
the guidance topics, the follow-up schedule — is computed from these fields. A
wrong value here is not a wrong sentence on a screen; it silently changes a risk
calculation, and nobody knows to question it.

So the tests are almost entirely about refusal:

1. EVERY VALUE NEEDS A QUOTE THAT IS ACTUALLY IN THE DOCUMENT. This is the one
   check that separates "read from the summary" from "plausible for a
   74-year-old". A model that invents a value must also invent a quote, and an
   invented quote fails a substring test.
2. NOT FOUND MEANS BLANK. Never a typical value, never an inferred one.
3. ENUMS COME FROM THE SCHEMA. A value outside the model's own options cannot
   reach the form.
4. `patient_ref` IS NEVER EXTRACTED. Taking it from the document would import a
   real hospital number, which is what `database.py` forbids.

No test calls a real API. The provider is stubbed at `extraction.extract._call`.
"""

import json

import pytest

from extraction import EXTRACTABLE_FIELDS, extract_from_text
from extraction.extract import _allowed_values, _normalise, _verify

SUMMARY = """
DISCHARGE SUMMARY — Ward 3

74-year-old woman admitted following sudden onset right-sided weakness.
Symptoms began at 08:00, arrived in the emergency department at 14:30.
On arrival GCS 13, drowsy but rousable. BP 172/94.

Examination: right upper limb weakness (MRC 3/5) and expressive dysphasia.
Visual fields could not be tested due to drowsiness. No facial droop.

ECG showed atrial fibrillation. CT head performed before treatment showed an
established left MCA territory infarct. Classified as PACS.

Discharged on aspirin 300mg daily. Prophylactic enoxaparin during admission.
Daughter (primary carer) contactable on +91 98765 43210.
"""


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def _mock(monkeypatch, fields):
    """Stub the provider with whatever the 'model' supposedly returned."""
    monkeypatch.setattr("extraction.extract._call",
                        lambda system, user: json.dumps({"fields": fields}))


# ------------------------------------------ 1: the quote must be in the document
def test_a_value_without_a_quote_is_rejected(monkeypatch, enabled):
    _mock(monkeypatch, [{"name": "age", "value": 74, "source": ""}])
    result = extract_from_text(SUMMARY)

    assert result.fields == []
    assert result.rejected[0]["reason"] == "no supporting quote given"
    assert "age" in result.not_found


def test_an_invented_quote_is_rejected(monkeypatch, enabled):
    """The check the whole design rests on.

    A plausible value with a fabricated quote is exactly what a hallucination
    looks like, and it is caught because the quote is not in the document.
    """
    _mock(monkeypatch, [
        {"name": "systolic_bp", "value": 140,
         "source": "blood pressure was 140/80 on admission"},
    ])
    result = extract_from_text(SUMMARY)

    assert result.fields == []
    assert "does not appear" in result.rejected[0]["reason"]
    assert result.rejected[0]["value"] == 140


def test_a_real_quote_is_accepted(monkeypatch, enabled):
    _mock(monkeypatch, [
        {"name": "systolic_bp", "value": 172, "source": "BP 172/94"},
    ])
    result = extract_from_text(SUMMARY)

    assert len(result.fields) == 1
    assert result.fields[0].value == 172
    assert result.fields[0].source == "BP 172/94"


def test_line_breaks_inside_a_quote_do_not_reject_it(monkeypatch, enabled):
    """PDF extraction breaks lines mid-sentence. Requiring an exact match would
    reject correct extractions for a purely cosmetic reason."""
    _mock(monkeypatch, [
        {"name": "deficit_arm", "value": "present",
         "source": "right upper limb\n   weakness (MRC 3/5)"},
    ])
    result = extract_from_text(SUMMARY)
    assert len(result.fields) == 1


# ---------------------------------------------- 2: not found means blank
def test_fields_absent_from_the_document_are_reported_not_guessed(
        monkeypatch, enabled):
    _mock(monkeypatch, [{"name": "age", "value": 74, "source": "74-year-old woman"}])
    result = extract_from_text(SUMMARY)

    assert result.as_form_values() == {"age": 74}
    assert len(result.not_found) == len(EXTRACTABLE_FIELDS) - 1
    assert "systolic_bp" in result.not_found
    assert "heparin_last_24h" in result.not_found


def test_an_empty_value_is_not_an_extraction(monkeypatch, enabled):
    _mock(monkeypatch, [
        {"name": "stroke_subtype", "value": "", "source": "Classified as PACS"},
    ])
    result = extract_from_text(SUMMARY)
    assert result.fields == []


def test_a_missing_key_leaves_the_form_untouched(monkeypatch, enabled):
    """The most important negative case: no key means a blank form and a stated
    reason, never a partially-guessed one."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = extract_from_text(SUMMARY)

    assert result.fields == []
    assert result.as_form_values() == {}
    assert "ANTHROPIC_API_KEY" in result.error
    assert set(result.not_found) == set(EXTRACTABLE_FIELDS)


def test_a_provider_failure_is_reported_not_swallowed(monkeypatch, enabled):
    def boom(system, user):
        raise RuntimeError("rate limited")

    monkeypatch.setattr("extraction.extract._call", boom)
    result = extract_from_text(SUMMARY)

    assert result.ok is False
    assert "rate limited" in result.error
    assert result.fields == []


def test_a_malformed_response_fails_rather_than_being_repaired(
        monkeypatch, enabled):
    monkeypatch.setattr("extraction.extract._call",
                        lambda s, u: "here are the fields I found: age is 74")
    result = extract_from_text(SUMMARY)

    assert result.ok is False
    assert result.fields == []


def test_a_fenced_json_response_is_still_read(monkeypatch, enabled):
    """Models wrap JSON in ```json despite instructions. Worth tolerating —
    unlike a malformed response, there is no ambiguity about what was meant."""
    monkeypatch.setattr(
        "extraction.extract._call",
        lambda s, u: '```json\n{"fields": [{"name": "age", "value": 74, '
                     '"source": "74-year-old woman"}]}\n```')
    result = extract_from_text(SUMMARY)
    assert result.as_form_values() == {"age": 74}


def test_an_empty_document_is_refused_before_any_call(monkeypatch, enabled):
    monkeypatch.setattr(
        "extraction.extract._call",
        lambda s, u: pytest.fail("must not call the provider for an empty document"))
    result = extract_from_text("   ")
    assert result.ok is False


# ------------------------------------------------- 3: enums come from the schema
@pytest.mark.parametrize("name, bad", [
    ("consciousness", "semi-conscious"),
    ("stroke_subtype", "MCA"),
    ("atrial_fibrillation", "probable"),
    ("planned_heparin", "prophylactic"),
    ("deficit_arm", "mild"),
    ("sex", "female"),
])
def test_a_value_outside_the_schema_cannot_reach_the_form(
        monkeypatch, enabled, name, bad):
    """The model must speak the form's vocabulary, not its own. 'semi-conscious'
    is a reasonable English description and not one of three legal values."""
    _mock(monkeypatch, [{"name": name, "value": bad, "source": "GCS 13, drowsy"}])
    result = extract_from_text(SUMMARY)

    assert result.fields == []
    assert "not one of" in result.rejected[0]["reason"]


def test_allowed_values_are_derived_not_restated():
    """If these ever diverge from AssessmentRequest, extraction starts rejecting
    correct values and the symptom is a silently empty form."""
    from api.schemas import AssessmentRequest

    allowed = _allowed_values()
    assert allowed["consciousness"] == ["alert", "drowsy", "unconscious"]
    assert allowed["stroke_subtype"] == ["TACS", "PACS", "LACS", "POCS", "OTH"]
    assert allowed["deficit_arm"] == ["absent", "present", "cannot_assess"]

    # The real assertion: they match the model, whatever the model says.
    schema = AssessmentRequest.model_json_schema()
    defs = schema["$defs"]
    assert allowed["sex"] == defs["Sex"]["enum"]


# ------------------------------------------- 4: identifiers are never extracted
def test_patient_ref_is_never_taken_from_the_document(monkeypatch, enabled):
    """It would import a real hospital number, which `database.py` forbids. The
    reference is the clinician's own pseudonymous label, chosen by them."""
    _mock(monkeypatch, [
        {"name": "patient_ref", "value": "MRN-4471882",
         "source": "DISCHARGE SUMMARY — Ward 3"},
    ])
    result = extract_from_text(SUMMARY)

    assert result.fields == []
    assert result.rejected[0]["reason"] == "not a field we extract"
    assert "patient_ref" not in result.as_form_values()


def test_patient_ref_is_not_even_offered_to_the_model(monkeypatch, enabled):
    captured = {}

    def capture(system, user):
        captured["prompt"] = user
        return json.dumps({"fields": []})

    monkeypatch.setattr("extraction.extract._call", capture)
    extract_from_text(SUMMARY)

    assert "patient_ref" not in captured["prompt"]
    assert "caregiver_language" not in captured["prompt"]


def test_unknown_field_names_are_rejected(monkeypatch, enabled):
    _mock(monkeypatch, [
        {"name": "diagnosis", "value": "stroke", "source": "left MCA territory infarct"},
    ])
    result = extract_from_text(SUMMARY)
    assert result.fields == []


# ---------------------------------------------------- the three-state deficit
def test_cannot_assess_survives_as_a_distinct_answer(monkeypatch, enabled):
    """The summary says fields could not be tested. That is neither present nor
    absent, and the distinction carries real prognostic weight — 14-day mortality
    among unassessable visual fields exceeds that of confirmed deficits."""
    _mock(monkeypatch, [
        {"name": "deficit_visual_field", "value": "cannot_assess",
         "source": "Visual fields could not be tested due to drowsiness"},
        {"name": "deficit_face", "value": "absent", "source": "No facial droop"},
        {"name": "deficit_arm", "value": "present",
         "source": "right upper limb weakness"},
    ])
    values = extract_from_text(SUMMARY).as_form_values()

    assert values["deficit_visual_field"] == "cannot_assess"
    assert values["deficit_face"] == "absent"
    assert values["deficit_arm"] == "present"


# ----------------------------------------------------------------- reporting
def test_rejections_are_reported_rather_than_hidden(monkeypatch, enabled):
    """"It tried and I rejected it" is different information from "it found
    nothing", and a clinician deciding whether to trust autofill needs both."""
    _mock(monkeypatch, [
        {"name": "age", "value": 74, "source": "74-year-old woman"},
        {"name": "systolic_bp", "value": 140, "source": "BP was 140/80"},
    ])
    result = extract_from_text(SUMMARY)

    payload = result.to_json()
    assert payload["found_count"] == 1
    assert payload["extractable_count"] == len(EXTRACTABLE_FIELDS)
    assert len(payload["rejected"]) == 1
    assert "systolic_bp" in payload["not_found"]


def test_normalise_is_case_and_whitespace_insensitive_only():
    """Enough to survive PDF line wrapping, not so loose that a different
    sentence would match."""
    assert _normalise("BP  172/94\n") == _normalise("bp 172/94")
    assert _normalise("BP 172/94") != _normalise("BP 140/80")


def test_verify_accepts_a_quote_spanning_the_documents_line_breaks():
    allowed = _allowed_values()
    accepted, rejected = _verify(
        SUMMARY,
        {"name": "consciousness", "value": "drowsy",
         "source": "GCS 13, drowsy but rousable"},
        allowed)
    assert rejected is None
    assert accepted.value == "drowsy"


# =========================================================== the HTTP endpoint
SUMMARY_BYTES = SUMMARY.encode()


def _post(client, body=SUMMARY_BYTES, content_type="text/plain"):
    return client.post("/api/extract", content=body,
                       headers={"content-type": content_type})


def test_extraction_requires_a_session(monkeypatch):
    """It reads a clinical document and returns clinical values. An anonymous
    caller must not be able to use it as a free extraction service either."""
    from fastapi.testclient import TestClient

    from api.database import Base, engine
    from api.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    assert _post(TestClient(app)).status_code == 401


def test_pasted_text_is_extracted(client, monkeypatch, enabled):
    _mock(monkeypatch, [
        {"name": "age", "value": 74, "source": "74-year-old woman"},
        {"name": "systolic_bp", "value": 172, "source": "BP 172/94"},
    ])
    body = _post(client).json()

    assert body["found_count"] == 2
    assert {f["name"] for f in body["fields"]} == {"age", "systolic_bp"}
    assert all(f["source"] for f in body["fields"]), "every field carries its quote"


def test_extraction_creates_nothing(client, db, monkeypatch, enabled):
    """It returns values for a form. The clinician submits, not the extractor —
    autofill must never become a second way to create a patient."""
    from api.database import Assessment, Patient

    _mock(monkeypatch, [{"name": "age", "value": 74, "source": "74-year-old woman"}])
    _post(client)

    assert db.query(Patient).count() == 0
    assert db.query(Assessment).count() == 0


def test_an_empty_body_is_rejected(client, enabled):
    assert _post(client, body=b"").status_code == 400


def test_an_oversized_document_is_rejected(client, enabled):
    from api.main import MAX_DOCUMENT_BYTES

    assert _post(client, body=b"x" * (MAX_DOCUMENT_BYTES + 1)).status_code == 413


def test_a_scanned_pdf_says_so_rather_than_returning_an_empty_form(
        client, monkeypatch, enabled):
    """A scan is images with no text layer. Returning an empty form would leave
    the clinician wondering whether extraction failed or the summary was blank."""
    monkeypatch.setattr("extraction.text_from_pdf", lambda data: "   ")
    r = _post(client, body=b"%PDF-1.4 fake", content_type="application/pdf")

    assert r.status_code == 400
    assert "scan" in r.json()["detail"].lower()


def test_an_unreadable_pdf_is_reported_clearly(client, monkeypatch, enabled):
    def boom(data):
        raise ValueError("not a pdf")

    monkeypatch.setattr("extraction.text_from_pdf", boom)
    r = _post(client, body=b"not really a pdf", content_type="application/pdf")

    assert r.status_code == 400
    assert "paste the text" in r.json()["detail"].lower()


def test_a_text_pdf_is_read(client, monkeypatch, enabled):
    monkeypatch.setattr("extraction.text_from_pdf", lambda data: SUMMARY)
    _mock(monkeypatch, [{"name": "age", "value": 74, "source": "74-year-old woman"}])

    body = _post(client, body=b"%PDF-1.4 fake",
                 content_type="application/pdf").json()
    assert body["found_count"] == 1


# ======================================================= the evaluation harness
# `hours_since_onset` is the one field whose VALUE is not written in the summary.
# Summaries give two clock times — "began 08:00, reached casualty 14:30" — and
# the value is the difference. The evidence rule still holds: the quote is that
# sentence and it is verifiable; only the arithmetic belongs to the model. Every
# other numeric field is copied, so a gold label that is not in the text is a
# mistake in the gold set.
DERIVED_FIELDS = {"hours_since_onset"}


def test_the_gold_set_only_labels_what_the_text_supports():
    """A gold label the summary does not support would train the evaluation to
    reward guessing, which is the opposite of what it is for.

    Checked crudely — every labelled numeric value must appear as digits in the
    text — because a gold set nobody checks is worse than none. This test already
    earned its place: it caught `hours_since_onset` and forced the derived-field
    distinction to be made explicit rather than assumed.
    """
    import json

    from extraction.evaluate import CASES

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    assert payload["cases"], "gold set is empty"

    for case in payload["cases"]:
        text = case["text"]
        for name, value in case["expected"].items():
            if name in DERIVED_FIELDS:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                whole = str(int(value)) if float(value).is_integer() else str(value)
                assert whole in text, (
                    f"{case['id']}: labelled {name}={value} but that number is "
                    f"not in the summary. If it is derived rather than copied, "
                    f"add it to DERIVED_FIELDS and say why.")


def test_the_gold_set_uses_only_real_field_names():
    import json

    from extraction.evaluate import CASES

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    for case in payload["cases"]:
        unknown = set(case["expected"]) - set(EXTRACTABLE_FIELDS)
        assert not unknown, f"{case['id']}: unknown fields {unknown}"


def test_gold_values_are_legal_schema_values():
    """A gold label outside the schema could never be matched, so the field would
    score zero forever and look like a model failure."""
    import json

    from extraction.evaluate import CASES

    allowed = _allowed_values()
    payload = json.loads(CASES.read_text(encoding="utf-8"))
    for case in payload["cases"]:
        for name, value in case["expected"].items():
            options = allowed.get(name)
            if options and not isinstance(value, bool):
                assert str(value) in options, \
                    f"{case['id']}: {name}={value!r} is not one of {options}"


def test_missed_and_wrong_are_counted_separately():
    """The distinction the whole report rests on. A missed field costs seconds;
    a wrong one changes a risk tier. Collapsing them into one 'error rate' would
    let a change trading ten misses for one wrong value look like progress."""
    from extraction.evaluate import FieldTally

    missed_only = FieldTally(correct=9, missed=1)
    wrong_only = FieldTally(correct=9, wrong=1)

    assert missed_only.recall == wrong_only.recall == 0.9
    assert missed_only.precision == 1.0, "missing a value never makes one wrong"
    assert wrong_only.precision == 0.9


def test_an_invented_value_lowers_precision_without_touching_recall():
    """Spurious extraction is the failure this design exists to prevent, so it
    must be visible in the numbers rather than averaged away."""
    from extraction.evaluate import FieldTally

    clean = FieldTally(correct=5)
    invented = FieldTally(correct=5, spurious=5)

    assert clean.precision == 1.0
    assert invented.precision == 0.5
    assert invented.recall == clean.recall == 1.0, "recall is blind to inventions"


@pytest.mark.parametrize("expected, actual, same", [
    (6.5, "6.5", True),
    (74, 74.0, True),
    (True, True, True),
    ("present", "Present", True),
    (172, 140, False),
    ("present", "absent", False),
    (True, False, False),
])
def test_comparison_tolerates_json_types_but_not_different_values(
        expected, actual, same):
    """The model returns JSON and the gold file is hand-written, so a str/int
    mismatch is noise. A different value is not."""
    from extraction.evaluate import _equal

    assert _equal(expected, actual) is same


def test_every_added_gold_label_carries_a_justification():
    """Guards the one dangerous thing about this evaluation.

    The first real run scored 61/61 correct with 6 "spurious" extractions, and on
    inspection all six were supported by sentences I had failed to label. So the
    gold set was revised — which is exactly how an evaluation quietly becomes
    meaningless if done carelessly.

    The rule that keeps it honest: a case whose labels were revised must carry a
    `_justification` quoting the supporting sentence for each added field. If the
    justification cannot be written from the TEXT alone, the label does not go in.
    """
    import json

    from extraction.evaluate import CASES

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    for case in payload["cases"]:
        justification = case.get("_justification")
        if not justification:
            continue
        for name, reason in justification.items():
            assert name in case["expected"], \
                f"{case['id']}: justified {name} but it is not labelled"
            assert len(reason) > 40, \
                f"{case['id']}/{name}: justification is too thin to check"
            # The quoted sentence must actually be in the summary. A
            # justification that cannot be traced to the text is the failure
            # this test exists to catch.
            quoted = reason.split("'")
            assert len(quoted) > 2, \
                f"{case['id']}/{name}: justification must quote the summary"
            assert _normalise(quoted[1]) in _normalise(case["text"]), \
                f"{case['id']}/{name}: quoted sentence is not in the summary"
