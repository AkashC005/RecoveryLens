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

export interface AssessmentResponse {
  assessment_id: number;
  patient_id: number;
  created_at: string;
  risks: RiskResult[];
  guidance_triggers: string[];
  followup_plan: CheckInPlan[];
  disclaimer: string;
}

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
