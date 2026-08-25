"""
RecoveryLens — extraction/extract.py
====================================
Discharge summary in, assessment fields out, each with the quote it came from.

The schema is derived from AssessmentRequest rather than restated
-----------------------------------------------------------------
`FIELD_GUIDE` below describes how each field is WORDED in a discharge summary —
that part is knowledge a schema cannot hold. But the legal VALUES come from the
Pydantic model at import time. Restating the enums here would create two
definitions of `stroke_subtype` that drift apart, and the failure would be a
silently rejected extraction rather than an error.

The verification step
---------------------
The model is asked for a `source` quote alongside every value, and that quote is
then checked to actually appear in the document. This is not a formality: it is
the single guard that separates "read from the summary" from "plausible for a
74-year-old". A model that invents a value must also invent a quote, and an
invented quote fails a substring check.

Whitespace is normalised on both sides before comparing, because PDF extraction
inserts line breaks in the middle of sentences and an exact match would reject
correct extractions for a cosmetic reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
import json
import os
import re

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 4000

# A summary longer than this is either not a discharge summary or has an appendix
# that will not contain the fields anyway. Truncating keeps the cost bounded and
# the model's attention on the part that matters.
MAX_DOCUMENT_CHARS = 24_000

# How each field appears in real discharge summaries. The model needs this
# because the schema field names are our vocabulary, not a ward's — nothing in a
# discharge summary says "deficit_visuospatial".
FIELD_GUIDE: dict[str, str] = {
    "age": "Patient age in years.",
    "sex": "M or F.",
    "hours_since_onset": (
        "Hours between symptom onset and hospital arrival or assessment. Often "
        "written as 'symptoms began at 8am, presented 2pm' — compute the "
        "difference. If only 'last seen well' is given, use that."),
    "consciousness": (
        "alert / drowsy / unconscious. GCS 15 or 'fully conscious' is alert; "
        "GCS 9-14, 'drowsy', 'obtunded' is drowsy; GCS 8 or below is unconscious."),
    "systolic_bp": "The top number of the blood pressure at admission.",
    "stroke_subtype": (
        "Bamford/OCSP classification: TACS, PACS, LACS, POCS, or OTH. If the "
        "summary names it, use it. If it only gives territory (e.g. 'left MCA "
        "infarct'), do NOT guess the Bamford class — leave it out."),
    "symptoms_on_waking": "True only if the summary says symptoms were noticed on waking.",
    "deficit_face": "Facial weakness or droop.",
    "deficit_arm": "Arm or hand weakness. 'Right upper limb weakness' counts.",
    "deficit_leg": "Leg or foot weakness. 'Lower limb' counts.",
    "deficit_speech": "Dysphasia, aphasia, dysarthria, slurred speech.",
    "deficit_visual_field": "Hemianopia, visual field loss, field cut.",
    "deficit_visuospatial": "Neglect, inattention, visuospatial difficulty.",
    "deficit_brainstem": "Brainstem or cerebellar signs: ataxia, diplopia, vertigo, dysphagia of brainstem origin.",
    "deficit_other": "Any other focal deficit named in the summary.",
    "atrial_fibrillation": (
        "yes / no / unknown. 'AF', 'atrial fibrillation', 'irregularly irregular "
        "pulse' is yes. Explicitly 'sinus rhythm' is no. Silence is unknown — do "
        "NOT infer no from absence."),
    "ct_before_treatment": "True if a CT or MRI head was done before treatment started.",
    "infarct_visible_on_ct": "True only if imaging is reported as showing an infarct or established changes.",
    "heparin_last_24h": "True if heparin or LMWH was given in the 24h before admission.",
    "aspirin_last_3days": "True if aspirin was taken in the 3 days before admission.",
    "planned_aspirin": "True if aspirin is prescribed on discharge or planned.",
    "planned_heparin": (
        "none / low / medium. Prophylactic-dose LMWH is low; treatment-dose is "
        "medium; nothing mentioned is none."),
    "caregiver_contact": (
        "A phone number for a family member or carer, if the summary gives one. "
        "NOT the hospital's number and NOT the patient's own if it is labelled as "
        "theirs."),
}

# Deficits use a three-state answer and the third state carries real prognostic
# weight, so the model has to be told when to use it rather than defaulting.
DEFICIT_INSTRUCTION = (
    "For every deficit field use exactly one of:\n"
    "  present       — the summary says this deficit was there\n"
    "  absent        — the summary explicitly says it was NOT there\n"
    "  cannot_assess — the summary says it could not be tested (e.g. 'visual "
    "fields untestable due to drowsiness')\n\n"
    "If the summary simply does not mention a deficit, OMIT the field entirely. "
    "Silence is not the same as absence, and 'absent' is a clinical claim that "
    "someone looked and found nothing."
)

# Never extracted, for different reasons.
#   patient_ref        an identifier the CLINICIAN chooses; taking it from the
#                      document would import a real hospital number, which is
#                      exactly what database.py forbids
#   caregiver_language a preference nobody records in a discharge summary
EXCLUDED = {"patient_ref", "caregiver_language"}
EXTRACTABLE_FIELDS = tuple(FIELD_GUIDE)


class ExtractionUnavailable(RuntimeError):
    """No key, no SDK, or the provider failed. The form still works by hand."""


@dataclass
class ExtractedField:
    name: str
    value: object
    # The verbatim sentence from the document that supports this value. Required:
    # a field without one never leaves `extract_from_text`.
    source: str
    verified: bool = True

    def to_json(self) -> dict:
        return {"name": self.name, "value": self.value, "source": self.source}


@dataclass
class Extraction:
    fields: list[ExtractedField] = dc_field(default_factory=list)
    # Fields the model returned but which failed a check, with the reason. Shown
    # to the clinician rather than silently dropped: "it tried and I rejected it"
    # is different information from "it found nothing".
    rejected: list[dict] = dc_field(default_factory=list)
    not_found: list[str] = dc_field(default_factory=list)
    document_chars: int = 0
    provider: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_form_values(self) -> dict:
        return {f.name: f.value for f in self.fields}

    def to_json(self) -> dict:
        return {
            "fields": [f.to_json() for f in self.fields],
            "rejected": self.rejected,
            "not_found": self.not_found,
            "document_chars": self.document_chars,
            "provider": self.provider,
            "error": self.error,
            # Counted here so the UI does not have to derive it and get it wrong.
            "found_count": len(self.fields),
            "extractable_count": len(EXTRACTABLE_FIELDS),
        }


def extraction_enabled() -> bool:
    """Same posture as every other model-backed feature: off unless configured."""
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


# --------------------------------------------------------------- schema, derived
def _allowed_values() -> dict[str, list[str]]:
    """Legal enum values, read from AssessmentRequest at import time.

    Derived rather than restated. Two hand-maintained copies of `stroke_subtype`
    would drift, and the symptom would be an extraction silently rejected as
    invalid rather than an error anyone could see.
    """
    from api.schemas import AssessmentRequest

    schema = AssessmentRequest.model_json_schema()
    defs = schema.get("$defs", {})
    allowed: dict[str, list[str]] = {}

    for name, spec in schema.get("properties", {}).items():
        if name in EXCLUDED:
            continue
        refs = [spec] + spec.get("allOf", []) + spec.get("anyOf", [])
        for ref in refs:
            target = ref.get("$ref", "")
            if target.startswith("#/$defs/"):
                enum = defs.get(target.rsplit("/", 1)[-1], {}).get("enum")
                if enum:
                    allowed[name] = list(enum)
            elif "enum" in ref:
                allowed[name] = list(ref["enum"])
    return allowed


def _field_spec() -> str:
    """The field list handed to the model, with legal values inlined."""
    allowed = _allowed_values()
    lines = []
    for name, description in FIELD_GUIDE.items():
        options = allowed.get(name)
        if options:
            lines.append(f"- {name} (one of: {', '.join(options)}) — {description}")
        else:
            lines.append(f"- {name} — {description}")
    return "\n".join(lines)


SYSTEM = """You read a hospital discharge summary for a stroke patient and \
extract specific fields. You are filling in a form that a clinician will then \
check.

THE RULE THAT MATTERS MOST: for every field you return, you must also return a \
`source` — a short verbatim quote, copied exactly from the document, that \
supports the value. The quote is checked against the document. If you cannot \
find a quote, you must OMIT the field.

Omitting a field is always acceptable and often correct. A blank field costs the \
clinician a few seconds. A wrong field changes a risk calculation and nobody \
knows to question it. When you are unsure, leave it out.

Never infer a value from what is typical for a patient of this age or \
presentation. Only report what the document states.

Reply with a JSON object only. No preamble, no explanation, no markdown fences:

{"fields": [{"name": "age", "value": 74, "source": "74-year-old woman"}, ...]}

Values must match the type and allowed options given for each field. Booleans \
are true/false, numbers are unquoted."""


def _normalise(text: str) -> str:
    """Collapse whitespace for comparison. PDF extraction breaks lines mid-
    sentence, so an exact match would reject correct extractions cosmetically."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _verify(document: str, item: dict, allowed: dict[str, list[str]]
            ) -> tuple[ExtractedField | None, dict | None]:
    """Accept one extracted field, or say why not.

    Four checks, each closing a different way a wrong value reaches the form.
    """
    name = str(item.get("name", "")).strip()
    source = str(item.get("source", "") or "").strip()
    value = item.get("value")

    if name not in FIELD_GUIDE:
        # Includes anything in EXCLUDED — patient_ref must come from the
        # clinician, never from the document.
        return None, {"name": name, "reason": "not a field we extract"}

    if not source:
        return None, {"name": name, "value": value,
                      "reason": "no supporting quote given"}

    if _normalise(source) not in _normalise(document):
        # The check that makes the whole design work: an invented value needs an
        # invented quote, and an invented quote is not in the document.
        return None, {"name": name, "value": value, "source": source,
                      "reason": "quote does not appear in the document"}

    options = allowed.get(name)
    if options is not None and str(value) not in options:
        return None, {"name": name, "value": value,
                      "reason": f"not one of {', '.join(options)}"}

    if value is None or value == "":
        return None, {"name": name, "reason": "empty value"}

    return ExtractedField(name=name, value=value, source=source), None


def extract_from_text(document: str) -> Extraction:
    """Read a discharge summary. Never raises — a failure is a blank form."""
    document = (document or "").strip()
    result = Extraction(document_chars=len(document))

    if not document:
        result.error = "the document was empty"
        result.not_found = list(EXTRACTABLE_FIELDS)
        return result

    if not extraction_enabled():
        result.error = ("autofill needs ANTHROPIC_API_KEY; fill the form by hand")
        result.not_found = list(EXTRACTABLE_FIELDS)
        return result

    truncated = document[:MAX_DOCUMENT_CHARS]
    allowed = _allowed_values()
    prompt = (f"Fields to extract:\n\n{_field_spec()}\n\n"
              f"{DEFICIT_INSTRUCTION}\n\n"
              f"---- DISCHARGE SUMMARY ----\n{truncated}\n---- END ----")

    try:
        raw = _call(SYSTEM, prompt)
        payload = _parse(raw)
    except Exception as exc:
        result.error = f"extraction failed ({type(exc).__name__}: {exc})"[:200]
        result.not_found = list(EXTRACTABLE_FIELDS)
        return result

    result.provider = _model()
    for item in payload.get("fields", []):
        if not isinstance(item, dict):
            continue
        accepted, rejected = _verify(truncated, item, allowed)
        if accepted:
            result.fields.append(accepted)
        elif rejected:
            result.rejected.append(rejected)

    found = {f.name for f in result.fields}
    result.not_found = [name for name in EXTRACTABLE_FIELDS if name not in found]
    return result


def _parse(raw: str) -> dict:
    """Tolerate a markdown fence around the JSON, but nothing more.

    Models sometimes wrap JSON in ```json despite being told not to. That is
    worth handling. Anything beyond it is a malformed response and should fail
    loudly rather than being repaired into something plausible.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _model() -> str:
    return os.getenv("RECOVERYLENS_LLM_MODEL", DEFAULT_MODEL)


def _call(system: str, user: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ExtractionUnavailable("ANTHROPIC_API_KEY is not set")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=_model(), max_tokens=MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "\n".join(b.text for b in resp.content
                     if getattr(b, "type", "") == "text").strip()


def text_from_pdf(data: bytes) -> str:
    """Extract text from an uploaded PDF.

    Reuses the same `pypdf` path as the ISA guideline source. Imported here
    rather than at module level so a missing install disables PDF upload alone
    and leaves pasted text working.
    """
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)
