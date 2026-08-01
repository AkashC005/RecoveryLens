/**
 * RecoveryLens — API client
 *
 * Types mirror api/schemas.py. If you change a Pydantic model, change it here
 * too — there is no codegen step, deliberately, because one more build tool is
 * one more thing to break the night before a demo.
 */

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// --------------------------------------------------------------------- enums
export type Sex = "M" | "F";
export type Consciousness = "alert" | "drowsy" | "unconscious";
export type DeficitState = "absent" | "present" | "cannot_assess";
export type AtrialFib = "yes" | "no" | "unknown";
export type StrokeSubtype = "TACS" | "PACS" | "LACS" | "POCS" | "OTH";
export type HeparinPlan = "none" | "low" | "medium";
export type Tier = "low" | "moderate" | "elevated" | "high";
export type Actionability = "actionable" | "vigilance" | "exploratory";

// ------------------------------------------------------------------- request
export interface AssessmentRequest {
  patient_ref?: string | null;
  age: number;
  sex: Sex;
  hours_since_onset: number;
  consciousness: Consciousness;
  systolic_bp: number;
  stroke_subtype: StrokeSubtype;
  symptoms_on_waking: boolean;
  deficit_face: DeficitState;
  deficit_arm: DeficitState;
  deficit_leg: DeficitState;
  deficit_speech: DeficitState;
  deficit_visual_field: DeficitState;
  deficit_visuospatial: DeficitState;
  deficit_brainstem: DeficitState;
  deficit_other: DeficitState;
  atrial_fibrillation: AtrialFib;
  ct_before_treatment: boolean;
  infarct_visible_on_ct: boolean;
  heparin_last_24h: boolean;
  aspirin_last_3days: boolean;
  planned_aspirin: boolean;
  planned_heparin: HeparinPlan;
  caregiver_contact?: string | null;
}

// ------------------------------------------------------------------ response
export interface Driver {
  factor: string;
  direction: "increases" | "decreases";
  magnitude: number;
}

export interface RiskResult {
  outcome: string;
  label: string;
  horizon_days: number;
  probability: number;
  percentile: number;
  tier: Tier;
  actionability: Actionability;
  note: string;
  drivers: Driver[];
}

export interface CheckInPlan {
  day: number;
  reason: string;
}

// ------------------------------------------------------------------ guidance
export type GuidanceStatus = "covered" | "evidence_gap";
export type SourceTier = "primary" | "fallback";

export interface GuidanceSourceRef {
  id: string;
  tier: SourceTier;
  short_title: string;
  title: string;
  publisher: string;
  published: string;
  jurisdiction: string;
  retrieved: string;
  scope_caveat: string;
}

export interface GuidanceEntry {
  id: string;
  /** Recommendation number in the source, e.g. "1.13.10". */
  section: string;
  heading: string;
  /** VERBATIM guideline text. Must never be rendered as RecoveryLens's words. */
  excerpt: string;
  caveat: string | null;
  url: string;
  source: GuidanceSourceRef;
}

export interface GuidanceBlock {
  trigger: string;
  label: string;
  status: GuidanceStatus;
  audience: "clinician" | "caregiver";
  /** OUR wording, not a guideline's. Render it visually distinct from excerpts. */
  plain_summary: string;
  plain_summary_is_authored_by_recoverylens: boolean;
  evidence_note: string | null;
  entries: GuidanceEntry[];
  entry_count: number;
}

export interface GuidanceSourceListing {
  id: string;
  short_title: string;
  title: string;
  publisher: string;
  url: string;
  tier: SourceTier;
  published: string;
  jurisdiction: string;
  licence_note: string;
}

export interface GuidanceBundle {
  guidance: GuidanceBlock[];
  unresolved_triggers: string[];
  evidence_gaps: string[];
  sources_cited: GuidanceSourceListing[];
  retrieval_method: "deterministic_lookup";
  disclaimer: string;
}

export interface RetrievedPassage extends GuidanceEntry {
  trigger: string;
  relevance: number;
  cosine: number;
}

export interface GuidanceAnswer {
  question: string;
  /** False = nothing cleared the relevance floor. The retriever declined. */
  answered: boolean;
  mode: "extractive" | "synthesised" | "refusal";
  answer: string;
  passages: RetrievedPassage[];
  sources_cited: GuidanceSourceListing[];
  related_evidence_gaps: string[];
  disclaimer: string;
}

/** Provenance of the check-in INTERVAL itself — not of the guidance text. */
export type IntervalBasis =
  | "guideline"          // a published recommendation names this interval
  | "trial_convention"   // a research endpoint, not a care recommendation
  | "operational"        // our scheduling choice, no external backing
  | "unregistered";      // scheduled but missing from the evidence file — a bug

export interface CheckInCitation extends GuidanceEntry {
  source_id: string;
}

export interface EnrichedCheckIn {
  /** Deterministic. Set by rules, never by a model. */
  day: number;
  label: string;
  reason: string;
  basis: IntervalBasis;
  basis_explained: string;
  citations: CheckInCitation[];
  passages: RetrievedPassage[];
  evidence_note: string | null;
  clinician_note: string;
  caregiver_message: string;
  narrative_mode: "synthesised" | "static";
}

export interface AssessmentResponse {
  assessment_id: number;
  patient_id: number;
  created_at: string;
  risks: RiskResult[];
  guidance_triggers: string[];
  guidance: GuidanceBundle;
  followup_plan: CheckInPlan[];
  timeline: EnrichedCheckIn[];
  disclaimer: string;
}

/** Label + styling per basis. `operational` and `trial_convention` deliberately
 *  look different from `guideline` — the whole point is that a reader can tell
 *  at a glance which check-ins carry published backing and which are ours. */
export const BASIS_META: Record<IntervalBasis, { label: string; className: string }> = {
  guideline:        { label: "Guideline-backed", className: "border-teal-dim text-teal" },
  trial_convention: { label: "Trial convention", className: "border-raised text-muted" },
  operational:      { label: "Our scheduling",   className: "border-raised text-muted" },
  unregistered:     { label: "Unregistered",     className: "border-signal/50 text-signal" },
};

export interface PatientSummary {
  id: number;
  patient_ref: string | null;
  created_at: string;
  assessment_count: number;
  latest_tier_summary: Record<string, string> | null;
}

// -------------------------------------------------------------------- client
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* response had no JSON body — keep the status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; models_loaded: number }>("/health"),

  assess: (payload: AssessmentRequest) =>
    request<AssessmentResponse>("/api/assess", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  patients: () => request<PatientSummary[]>("/api/patients"),

  metrics: () => request<Record<string, unknown>>("/api/meta/metrics"),

  /** Clinician Q&A over the guidance corpus. May legitimately decline. */
  askGuidance: (question: string, top_k = 4) =>
    request<GuidanceAnswer>("/api/guidance/ask", {
      method: "POST",
      body: JSON.stringify({ question, top_k }),
    }),

  /** Corpus coverage + sources used and rejected. Backs the Evidence screen. */
  guidanceCoverage: () => request<Record<string, unknown>>("/api/guidance"),
};

// -------------------------------------------------------------------- tokens
/** Tier -> colour. Only `elevated` and `high` earn a signal colour. */
export const TIER_STYLES: Record<Tier, { dot: string; text: string; ring: string }> = {
  low:      { dot: "bg-muted",  text: "text-muted",  ring: "ring-raised" },
  moderate: { dot: "bg-teal",   text: "text-teal",   ring: "ring-teal-dim" },
  elevated: { dot: "bg-amber",  text: "text-amber",  ring: "ring-amber/40" },
  high:     { dot: "bg-signal", text: "text-signal", ring: "ring-signal/50" },
};

export const TIER_LABEL: Record<Tier, string> = {
  low: "Low",
  moderate: "Moderate",
  elevated: "Elevated",
  high: "High",
};

/** Horizon in days -> the timeline heading it sits under. */
export function horizonLabel(days: number): string {
  if (days <= 14) return "Within 14 days";
  if (days <= 90) return "Within 3 months";
  return "By 6 months";
}
