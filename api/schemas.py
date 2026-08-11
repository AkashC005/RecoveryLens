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
    trigger: str | None = Field(
        None,
        description="The guidance topic this passage belongs to, or null for "
                    "auto-ingested chunks. Null is not a defect: chunks from "
                    "corpus_full.json belong to no trigger by design, and that "
                    "is what keeps them out of the deterministic patient-facing "
                    "path, which filters on trigger membership.")
    relevance: float = Field(
        ..., description="Ranking score after length and fragment priors.")
    cosine: float = Field(..., description="Raw TF-IDF cosine, before priors.")
    blended: bool = Field(
        False, description="Whether the semantic blend contributed to `cosine`. "
                           "Determines which thresholds apply to it.")
    extraction: Literal["curated", "automatic"] = Field(
        "curated",
        description="'curated' = hand-verified section number. 'automatic' = "
                    "parsed from the source document, not human-checked.")


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


# Defined here, after RetrievedPassage, because EnrichedCheckIn embeds it.
class CheckInCitation(GuidanceEntry):
    """A guideline recommendation that justifies this check-in interval."""
    source_id: str


class EnrichedCheckIn(BaseModel):
    """One scheduled check-in, with its evidence and both narratives.

    `day` is deterministic — set by rules in Predictor._followup(), never by a
    model. Only `clinician_note` and `caregiver_message` are generated.
    """
    day: int
    label: str
    reason: str
    basis: Literal["guideline", "trial_convention", "operational", "unregistered"] = Field(
        ..., description="Provenance of the INTERVAL itself. 'operational' means "
                         "we chose this timing; no guideline recommends it.")
    basis_explained: str
    citations: list[CheckInCitation] = Field(
        ..., description="Recommendations naming this interval. Empty unless "
                         "basis is 'guideline'.")
    passages: list[RetrievedPassage] = Field(
        ..., description="Guidance retrieved for THIS patient's active triggers, "
                         "used to ground the narratives below.")
    evidence_note: str | None = None
    clinician_note: str
    caregiver_message: str = Field(
        ..., description="Plain language, generated under stricter rules than the "
                         "clinician text: no dosing, no diagnosis, no prognosis, "
                         "and escalation advice only where a passage supports it.")
    narrative_mode: Literal["synthesised", "static"] = Field(
        ..., description="'static' means generation was off or unavailable and "
                         "the fixed reason text was used instead.")


class TopicSelection(BaseModel):
    topic: str
    rationale: str = Field(
        ..., description="Why THIS patient needs this topic, in the agent's words.")
    source: Literal["agent", "rule"] = Field(
        ..., description="'rule' means the deterministic rules chose it and the "
                         "agent did not — it is included regardless.")


class GuidanceSelection(BaseModel):
    """How the guidance topics for this patient were chosen.

    The deterministic rules always run first; `rule_topics` is the floor the
    agent cannot drop below. The agent may add topics the rules miss — notably
    from the four deficits the rules never inspect — and orders by priority.
    """
    triggers: list[str]
    selections: list[TopicSelection]
    rule_topics: list[str]
    mode: Literal["rules", "agent", "agent_failed"]
    agent_summary: str = ""
    tool_calls: list[dict] = []
    agent_error: str | None = None


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
        ..., description="Cited guideline text for each trigger. The excerpts are "
                         "always verbatim; only the SELECTION of topics may be "
                         "agent-driven — see `guidance_selection`.")
    guidance_selection: GuidanceSelection = Field(
        ..., description="How those topics were chosen, and why.")
    followup_plan: list[CheckInPlan] = Field(
        ..., description="Raw deterministic schedule. Kept for backwards "
                         "compatibility; prefer `timeline` below.")
    timeline: list[EnrichedCheckIn] = Field(
        ..., description="The same days, each with its evidence basis, citations, "
                         "retrieved guidance and both narratives.")
    disclaimer: str


class PatientSummary(BaseModel):
    id: int
    patient_ref: str | None
    created_at: datetime
    assessment_count: int
    latest_tier_summary: dict[str, str] | None = None

    # Enough state to trage a list without opening every row. A patient with an
    # open escalation and one with none should not look identical.
    open_escalations: int = 0
    next_check_in: datetime | None = None
    consent_recorded: bool = False
    opted_out: bool = False


# ------------------------------------------------------------- patient detail
class MessagingState(BaseModel):
    """Whether this patient's carer can be messaged, and why not if not.

    `can_send` and `blocked_reason` are the return value of
    `messaging.policy.may_send()` — the same call `POST /api/checkins/{id}/send`
    makes, not a reimplementation. A screen that derives its own answer will
    eventually disagree with the gate that actually runs, and the disagreement
    will surface as "the UI said it would send and nothing arrived".
    """
    caregiver_contact_on_file: bool
    contact_hint: str | None = Field(
        None,
        description="Last four digits only. The full number is deliberately not "
                    "returned to a screen that gets demonstrated and "
                    "screenshotted; it is in the database and in the send "
                    "preview, where it is actually needed.")
    consent_recorded: bool
    opted_out: bool
    opted_out_at: datetime | None = None
    last_inbound_at: datetime | None = None
    whatsapp_window_open: bool = Field(
        ..., description="True if the carer messaged us within 24h. Outside the "
                         "window WhatsApp permits approved templates only, and "
                         "free-form sends fail with [21654].")
    whatsapp_window_note: str
    can_send: bool
    blocked_reason: str | None = None


class AssessmentRecord(BaseModel):
    """One assessment, as submitted and as scored.

    `inputs` is returned alongside `results` because a risk tier with no visible
    input is not reviewable — a clinician disagreeing with a tier needs to see
    what was entered before deciding whether the model or the data is wrong.
    """
    id: int
    created_at: datetime
    inputs: dict | None = None
    results: dict | None = None
    guidance_triggers: list[str] = []


CheckInStatus = Literal["completed", "sent", "overdue", "scheduled"]


class CheckInRecord(BaseModel):
    id: int
    scheduled_for: datetime
    sent_at: datetime | None = None
    completed_at: datetime | None = None
    reason: str | None = None
    status: CheckInStatus = Field(
        ..., description="Derived, not stored. 'sent' means sent and awaiting a "
                         "reply; 'overdue' means the date passed and nothing "
                         "went out — which is a scheduler or policy problem, not "
                         "a carer one.")
    responses: dict | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    urgency: Literal["routine", "soon", "urgent"] = "routine"
    triage: dict | None = Field(
        None, description="Full triage record: rule reasons, agent reasons, "
                          "urgency, agent summary and tool trace.")


class PatientDetail(BaseModel):
    id: int
    patient_ref: str | None
    created_at: datetime
    messaging: MessagingState
    assessments: list[AssessmentRecord] = Field(
        ..., description="Newest first.")
    check_ins: list[CheckInRecord] = Field(
        ..., description="Chronological, so the follow-up path reads forwards.")
    latest_tier_summary: dict[str, str] | None = None
    open_escalations: int = 0
    next_check_in: datetime | None = None


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