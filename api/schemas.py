"""
RecoveryLens API — schemas.py
=============================
Request and response shapes.

The input speaks clinical language ("drowsy", "cannot_assess"), not model column
names. Translation to the feature vector happens in predictor.py, so the UI and
the model can evolve independently.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- enums
class Sex(str, Enum):
    male = "M"
    female = "F"


class Consciousness(str, Enum):
    alert = "alert"
    drowsy = "drowsy"
    unconscious = "unconscious"


class DeficitState(str, Enum):
    absent = "absent"
    present = "present"
    cannot_assess = "cannot_assess"


class AtrialFib(str, Enum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class StrokeSubtype(str, Enum):
    TACS = "TACS"
    PACS = "PACS"
    LACS = "LACS"
    POCS = "POCS"
    OTH = "OTH"


class HeparinPlan(str, Enum):
    none = "none"
    low = "low"
    medium = "medium"


class Tier(str, Enum):
    low = "low"
    moderate = "moderate"
    elevated = "elevated"
    high = "high"


# --------------------------------------------------------------------------- request
class AssessmentRequest(BaseModel):
    """Everything recorded at discharge. Optional fields default to the most
    common value so a rushed ward entry still produces a usable result."""

    patient_ref: str | None = Field(
        None, description="Your own identifier. Never send a real patient name.")

    # demographics
    age: int = Field(..., ge=16, le=110)
    sex: Sex

    # presentation
    hours_since_onset: float = Field(..., ge=0, le=48)
    consciousness: Consciousness
    systolic_bp: int = Field(..., ge=60, le=300)
    stroke_subtype: StrokeSubtype
    symptoms_on_waking: bool = False

    # deficits
    deficit_face: DeficitState = DeficitState.absent
    deficit_arm: DeficitState = DeficitState.absent
    deficit_leg: DeficitState = DeficitState.absent
    deficit_speech: DeficitState = DeficitState.absent
    deficit_visual_field: DeficitState = DeficitState.absent
    deficit_visuospatial: DeficitState = DeficitState.absent
    deficit_brainstem: DeficitState = DeficitState.absent
    deficit_other: DeficitState = DeficitState.absent

    # history and imaging
    atrial_fibrillation: AtrialFib = AtrialFib.unknown
    ct_before_treatment: bool = False
    infarct_visible_on_ct: bool = False
    heparin_last_24h: bool = False
    aspirin_last_3days: bool = False

    # planned treatment
    planned_aspirin: bool = False
    planned_heparin: HeparinPlan = HeparinPlan.none

    # caregiver contact, for the follow-up system
    caregiver_contact: str | None = Field(
        None, description="Phone or email for check-ins. Requires consent.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "patient_ref": "ward3-014",
                "age": 74, "sex": "F",
                "hours_since_onset": 6.5,
                "consciousness": "drowsy",
                "systolic_bp": 172,
                "stroke_subtype": "PACS",
                "deficit_arm": "present",
                "deficit_speech": "present",
                "deficit_visual_field": "cannot_assess",
                "atrial_fibrillation": "yes",
                "ct_before_treatment": True,
                "planned_aspirin": True,
                "planned_heparin": "low",
            }
        }
    }


# --------------------------------------------------------------------------- response
class Driver(BaseModel):
    """One contributing factor, in clinician language."""
    factor: str
    direction: Literal["increases", "decreases"]
    magnitude: float = Field(..., description="Absolute SHAP contribution.")


class RiskResult(BaseModel):
    outcome: str
    label: str
    horizon_days: int
    probability: float = Field(
        ..., description="Model output. Trained on 1991-96 data — use the tier "
                         "for decisions, not this number.")
    percentile: int = Field(..., ge=0, le=100)
    tier: Tier
    actionability: Literal["actionable", "vigilance", "exploratory"]
    note: str
    drivers: list[Driver]


class CheckInPlan(BaseModel):
    day: int
    reason: str


# --------------------------------------------------------------------- guidance
class GuidanceSourceRef(BaseModel):
    """Provenance for a single excerpt. Every field here exists so a reader can
    independently verify the quote against the published document."""
    id: str
    tier: Literal["primary", "fallback"]
    short_title: str
    title: str
    publisher: str
    published: str = ""
    jurisdiction: str = ""
    retrieved: str = ""
    scope_caveat: str = ""


class GuidanceEntry(BaseModel):
    id: str
    section: str = Field(..., description="Recommendation or section number in the source.")
    heading: str = ""
    excerpt: str = Field(
        ..., description="VERBATIM from the source. Never generated, never paraphrased.")
    caveat: str | None = Field(
        None, description="Limitation on how this excerpt should be read.")
    url: str
    source: GuidanceSourceRef


class GuidanceBlock(BaseModel):
    trigger: str
    label: str
    status: Literal["covered", "evidence_gap"] = Field(
        ..., description="'evidence_gap' means no verified source makes a "
                         "recommendation here. Entries will be empty by design.")
    audience: Literal["clinician", "caregiver"]
    plain_summary: str
    plain_summary_is_authored_by_recoverylens: bool = Field(
        True, description="Always true. The UI must render plain_summary visually "
                          "distinct from excerpts so guideline text is never "
                          "confused with ours.")
    evidence_note: str | None = None
    entries: list[GuidanceEntry]
    entry_count: int


class GuidanceSourceListing(BaseModel):
    id: str
    short_title: str
    title: str
    publisher: str
    url: str
    tier: str
    published: str = ""
    jurisdiction: str = ""
    licence_note: str = ""


class GuidanceBundle(BaseModel):
    guidance: list[GuidanceBlock]
    unresolved_triggers: list[str] = Field(
        ..., description="Triggers the predictor emitted that the corpus cannot "
                         "resolve. Should always be empty; non-empty means the "
                         "predictor and corpus have drifted apart.")
    evidence_gaps: list[str]
    sources_cited: list[GuidanceSourceListing]
    retrieval_method: Literal["deterministic_lookup"]
    disclaimer: str


class GuidanceQuestion(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    top_k: int = Field(4, ge=1, le=8)

    model_config = {
        "json_schema_extra": {
            "example": {"question": "Are wrist splints recommended after stroke?"}
        }
    }


class RetrievedPassage(GuidanceEntry):
    trigger: str
    relevance: float = Field(
        ..., description="Ranking score after length and fragment priors.")
    cosine: float = Field(..., description="Raw TF-IDF cosine, before priors.")


class GuidanceAnswer(BaseModel):
    question: str
    answered: bool = Field(
        ..., description="False means nothing cleared the relevance floor. The "
                         "retriever declines rather than returning its best guess.")
    mode: Literal["extractive", "synthesised", "refusal"]
    answer: str
    passages: list[RetrievedPassage]
    sources_cited: list[GuidanceSourceListing]
    related_evidence_gaps: list[str] = []
    disclaimer: str


class AssessmentResponse(BaseModel):
    assessment_id: int
    patient_id: int
    patient_created: bool = Field(
        ..., description="True if this created a new patient record, False if it "
                         "re-assessed an existing one.")
    assessment_number: int = Field(
        ..., description="How many assessments this patient now has. 1 = first.")
    created_at: datetime
    risks: list[RiskResult]
    guidance_triggers: list[str] = Field(
        ..., description="Content categories for the guidance layer to retrieve.")
    guidance: GuidanceBundle = Field(
        ..., description="Cited guideline text for each trigger, resolved by "
                         "deterministic lookup. Nothing here is generated.")
    followup_plan: list[CheckInPlan]
    disclaimer: str


class PatientSummary(BaseModel):
    id: int
    patient_ref: str | None
    created_at: datetime
    assessment_count: int
    latest_tier_summary: dict[str, str] | None = None


class CheckInResponse(BaseModel):
    id: int
    patient_id: int
    scheduled_for: datetime
    completed_at: datetime | None
    escalated: bool
    responses: dict | None


class CheckInSubmission(BaseModel):
    taking_medication: bool
    new_symptoms: bool
    worse_than_last_week: bool
    free_text: str | None = None