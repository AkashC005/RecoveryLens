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
  /** REQUIRED. How the patient appears in every list, escalation and carer link.
   *  A ward reference or study number — never a real name. */
  patient_ref: string;
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
  /** Language for messages sent to the CARER. Clinician-facing text and quoted
   *  guideline excerpts stay English regardless. */
  caregiver_language?: "en" | "ta" | "hi" | null;
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
  /** NULL for auto-ingested chunks, which belong to no topic. This was typed as
   *  `string` while the backend already returned null, and the mismatch surfaced
   *  as a 500 on the Ask box rather than as a type error — there is no codegen
   *  step, so nothing was checking. */
  trigger: string | null;
  relevance: number;
  cosine: number;
  /** Whether semantic similarity contributed. Determines which floor applied. */
  blended: boolean;
  /** 'curated' = hand-verified section number. 'automatic' = parsed. */
  extraction: "curated" | "automatic";
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

export interface TopicSelection {
  topic: string;
  /** Why THIS patient needs it, in the agent's words. */
  rationale: string;
  /** 'rule' = the deterministic rules chose it and the agent did not. */
  source: "agent" | "rule";
}

export interface GuidanceSelection {
  triggers: string[];
  selections: TopicSelection[];
  /** The floor. These are present whatever the agent decided. */
  rule_topics: string[];
  mode: "rules" | "agent" | "agent_failed";
  agent_summary: string;
  tool_calls: { name: string; arguments: Record<string, unknown> }[];
  agent_error: string | null;
}

export interface AssessmentResponse {
  assessment_id: number;
  patient_id: number;
  /** True if this created a new patient rather than re-assessing one. */
  patient_created: boolean;
  /** How many assessments this patient now has. 1 = first. */
  assessment_number: number;
  created_at: string;
  risks: RiskResult[];
  guidance_triggers: string[];
  guidance: GuidanceBundle;
  guidance_selection: GuidanceSelection;
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
  open_escalations: number;
  next_check_in: string | null;
  consent_recorded: boolean;
  opted_out: boolean;
}

// ------------------------------------------------------------ patient detail
/** Whether the carer can be messaged. `can_send` and `blocked_reason` come from
 *  the same `may_send()` call the send endpoint makes — never re-derive them
 *  here, or this screen will eventually promise a send the gate refuses. */
export interface MessagingState {
  caregiver_contact_on_file: boolean;
  /** Last four digits only, by design. */
  contact_hint: string | null;
  consent_recorded: boolean;
  opted_out: boolean;
  opted_out_at: string | null;
  last_inbound_at: string | null;
  whatsapp_window_open: boolean;
  whatsapp_window_note: string;
  can_send: boolean;
  blocked_reason: string | null;
}

export interface AssessmentRecord {
  id: number;
  created_at: string;
  /** The request as submitted. Present so a tier can be argued with. */
  inputs: Partial<AssessmentRequest> | null;
  results: { risks?: RiskResult[] } | null;
  guidance_triggers: string[];
}

/** `overdue` is ours to fix (scheduler off, consent missing, rate limited).
 *  `sent` is the carer's to answer. Collapsing them hides the actionable one. */
export type CheckInStatus = "completed" | "sent" | "overdue" | "scheduled";

export interface CheckInRecord {
  id: number;
  scheduled_for: string;
  sent_at: string | null;
  completed_at: string | null;
  reason: string | null;
  status: CheckInStatus;
  responses: Record<string, unknown> | null;
  escalated: boolean;
  escalation_reason: string | null;
  urgency: Urgency;
  triage: TriageRecord | null;
}

export interface PatientDetail {
  id: number;
  patient_ref: string | null;
  created_at: string;
  messaging: MessagingState;
  /** Newest first. */
  assessments: AssessmentRecord[];
  /** Chronological, so follow-up reads forwards. */
  check_ins: CheckInRecord[];
  latest_tier_summary: Record<string, string> | null;
  open_escalations: number;
  next_check_in: string | null;
}

export const CHECKIN_STATUS_META: Record<
  CheckInStatus,
  { label: string; className: string; hint: string }
> = {
  completed: {
    label: "Answered",
    className: "border-teal-dim text-teal",
    hint: "The carer replied and the response was triaged.",
  },
  sent: {
    label: "Awaiting reply",
    className: "border-amber/40 text-amber",
    hint: "Sent to the carer. No response yet.",
  },
  overdue: {
    label: "Not sent",
    className: "border-signal/50 text-signal",
    hint: "The date has passed and nothing went out. That is a scheduler or policy problem, not the carer's.",
  },
  scheduled: {
    label: "Scheduled",
    className: "border-raised text-muted",
    hint: "Due in the future. Nothing to do yet.",
  },
};

// -------------------------------------------------------------------- client
/** Thrown on 401 so the shell can show the sign-in screen instead of an error.
 *  A session expiring mid-session is normal, not a failure. */
export class NotSignedIn extends Error {}

/** Set when the page was opened from a carer's link (`/checkin?token=...`).
 *
 *  Carers do not have accounts. Their token is sent as a header on the three
 *  endpoints that accept it, and it grants access to exactly one check-in. */
let checkInToken: string | null = null;

export function setCheckInToken(token: string | null) {
  checkInToken = token;
}

export function getCheckInToken() {
  return checkInToken;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (checkInToken) headers["X-CheckIn-Token"] = checkInToken;

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    // The session is an httpOnly cookie, so JavaScript never holds the token and
    // an XSS bug cannot exfiltrate it. That only works if the cookie is actually
    // sent, which cross-origin fetch does not do by default.
    credentials: "include",
  });

  if (res.status === 401) throw new NotSignedIn("Not signed in");
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

// ------------------------------------------------------------------ autofill
/** One field read from a discharge summary, with the sentence it came from.
 *  The quote was verified against the document server-side — a value whose quote
 *  is not in the document never reaches here. */
export interface ExtractedField {
  name: string;
  value: unknown;
  source: string;
}

export interface Extraction {
  fields: ExtractedField[];
  /** Proposed and refused, with the reason. Usually the quote was not actually
   *  in the document, which is what a hallucination looks like from here.
   *  Reported rather than hidden: "it tried and I refused" is different
   *  information from "it found nothing". */
  rejected: { name: string; value?: unknown; source?: string; reason: string }[];
  /** Left BLANK, never guessed. A blank costs five seconds; a guess costs a
   *  wrong risk tier that nobody knows to question. */
  not_found: string[];
  found_count: number;
  extractable_count: number;
  document_chars: number;
  provider: string;
  error: string | null;
}

// ----------------------------------------------------------------------- auth
export interface AuthStatus {
  /** Whether registration is offered at all. */
  signup_open: boolean;
  /** Whether this deployment requires a shared registration code. Neither field
   *  reveals anything about who has an account here — no user count, no email,
   *  no organisation name. */
  signup_code_required: boolean;
}

export interface Me {
  id: number;
  email: string;
  full_name: string;
  organisation_id: number;
  organisation: string;
}

export const api = {
  health: () => request<{ status: string; models_loaded: number }>("/health"),

  authStatus: () => request<AuthStatus>("/api/auth/status"),
  me: () => request<Me>("/api/auth/me"),

  login: (email: string, password: string) =>
    request<Me>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  /** Create an account. Always available. Registering creates your OWN
   *  organisation, so what you enter is visible only to you until you invite
   *  someone into it. */
  signup: (email: string, password: string, organisation = "", code = "") =>
    request<Me & { adopted_existing_patients: number }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, organisation, code }),
    }),

  logout: () => request<{ signed_out: boolean }>("/api/auth/logout", {
    method: "POST",
  }),

  /** The carer's link for one check-in. Clinician-only to issue. */
  checkInLink: (checkinId: number) =>
    request<{ check_in_id: number; token: string; path: string }>(
      `/api/checkins/${checkinId}/link`),

  /** The single check-in a carer's token refers to. No session needed. */
  checkInByToken: (token: string) =>
    request<DueCheckIn>(`/api/checkins/by-token?token=${encodeURIComponent(token)}`),

  assess: (payload: AssessmentRequest) =>
    request<AssessmentResponse>("/api/assess", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  patients: () => request<PatientSummary[]>("/api/patients"),

  /** Read a discharge summary into assessment fields. Creates nothing — it
   *  returns values for a form the clinician then reviews and submits. */
  extract: (body: Blob | string, contentType: string) =>
    request<Extraction>("/api/extract", {
      method: "POST",
      headers: { "Content-Type": contentType },
      body,
    }),

  /** Full record for one patient: assessments with their inputs, every check-in
   *  with its triage trace, and whether the carer can be messaged at all. */
  patient: (id: number) => request<PatientDetail>(`/api/patients/${id}`),

  metrics: () => request<Record<string, unknown>>("/api/meta/metrics"),

  /** Clinician Q&A over the guidance corpus. May legitimately decline. */
  askGuidance: (question: string, top_k = 4) =>
    request<GuidanceAnswer>("/api/guidance/ask", {
      method: "POST",
      body: JSON.stringify({ question, top_k }),
    }),

  /** Corpus coverage + sources used and rejected. Backs the Evidence screen. */
  guidanceCoverage: () => request<Record<string, unknown>>("/api/guidance"),

  // ------------------------------------------------------------ follow-up
  /** `includeScheduled` also returns check-ins whose date has not arrived yet.
   *  Needed to demo the carer screen at all — the first real check-in is day 3. */
  dueCheckIns: (includeScheduled = false) =>
    request<DueCheckIn[]>(
      `/api/checkins/due${includeScheduled ? "?include_scheduled=true" : ""}`,
    ),

  submitCheckIn: (checkinId: number, body: CheckInSubmission) =>
    request<CheckInResult>(`/api/checkins/${checkinId}/respond`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  escalations: () => request<Escalation[]>("/api/escalations"),

  /** Upload a recording. Sent as a raw body rather than multipart, because the
   *  server deliberately does not install python-multipart. */
  submitVoiceNote: (checkinId: number, audio: Blob) =>
    request<VoiceUpload>(`/api/checkins/${checkinId}/voice`, {
      method: "POST",
      headers: { "Content-Type": audio.type || "audio/webm" },
      body: audio,
    }),

  /** Accept or reject the read-back. Only on acceptance does the text come back
   *  to be submitted with the rest of the form. */
  confirmVoiceNote: (checkinId: number, confirmed: boolean) =>
    request<VoiceConfirmation>(`/api/checkins/${checkinId}/voice/confirm`, {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    }),

  /** Which model-driven features are actually live. A silently disabled agent
   *  looks identical to one that found nothing — worth checking before a demo. */
  triageStatus: () => request<AiStatus>("/api/triage/status"),
};

// ------------------------------------------------------------------- status
/** Mirrors GET /api/triage/status.
 *
 *  NOTE: this shape changed when the endpoint grew from reporting one flag to
 *  reporting all three. The frontend was not updated at the time, so the Review
 *  tab read `status.agent_enabled` — which no longer existed — and displayed
 *  "Triage agent is off" while the agent was demonstrably running. Silent
 *  undefined is exactly the failure mode this endpoint exists to prevent, so
 *  the type is now explicit. */
export interface AiFeature {
  enabled: boolean;
  env: string;
  what: string;
  fallback: string;
}

export interface AiStatus {
  api_key_configured: boolean;
  features: {
    guidance_selection: AiFeature;
    checkin_narratives: AiFeature;
    triage_agent: AiFeature;
  };
  live: string[];
  all_live: boolean;
  note: string;
}

// ------------------------------------------------------------------- triage
export type Urgency = "routine" | "soon" | "urgent";

/** How a flag was raised. `rules_only` means the agent was off or unread. */
export type TriageMode = "rules_only" | "agent" | "agent_failed";

export interface DueCheckIn {
  id: number;
  patient_id: number;
  /** The label a human recognises. "#7" identifies the row in our database and
   *  nothing anyone on a ward knows about. */
  patient_ref: string | null;
  scheduled_for: string;
  completed_at: string | null;
  escalated: boolean;
  responses: Record<string, unknown> | null;
}

export interface CheckInSubmission {
  taking_medication: boolean;
  new_symptoms: boolean;
  worse_than_last_week: boolean;
  /** Free text. Until the triage agent existed, this was stored and never read. */
  free_text?: string | null;
}

export interface CheckInResult {
  check_in_id: number;
  escalated: boolean;
  escalation_reason: string | null;
  urgency: Urgency;
  triage_mode: TriageMode;
  rule_reasons: string[];
  agent_reasons: string[];
  message: string;
}

// --------------------------------------------------------------------- voice
/** Response from uploading a recording. `confirmed` is ALWAYS false here: the
 *  transcript is parked server-side, never recorded, until the read-back is
 *  confirmed. There is no confidence score high enough to skip that step. */
export interface VoiceUpload {
  check_in_id: number;
  confirmed: false;
  /** False when confidence was below the threshold, or the provider failed. */
  usable: boolean;
  /** The message to show the carer — a read-back, or a request to try again. */
  readback: string;
  transcript: string;
  confidence: number;
  /** The recording was hard to hear. The transcript is still shown — a human
   *  reading their own words back is a stronger check than a model's confidence
   *  score, which measures how sure it was and not whether it was right. */
  low_confidence: boolean;
  provider: string;
  /** Phrases where recognition characteristically drops a negation. "He can't
   *  move his arm" -> "He can move his arm" is fluent, plausible, and wrong in
   *  the reassuring direction — the one direction nothing downstream catches. */
  warnings: string[];
  /** False means RECOVERYLENS_VOICE is unset. Distinguishing that from "we could
   *  not hear you" matters: one is a config problem, and telling a carer to speak
   *  more clearly would be a lie. */
  voice_configured: boolean;
}

export interface VoiceConfirmation {
  check_in_id: number;
  confirmed: boolean;
  /** Empty unless confirmed. Returned for the form to submit, not written to the
   *  check-in here — `/respond` stays the only way to answer one. */
  transcript: string;
  message: string;
}

export interface TriageToolCall {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  error: string | null;
}

export interface TriageRecord {
  escalated: boolean;
  escalation_reason: string | null;
  /** Raised by the deterministic boolean checks. Never removable by the agent. */
  rule_reasons: string[];
  /** Added by the agent. Additive only. */
  agent_reasons: string[];
  urgency: Urgency;
  agent_summary: string;
  tool_calls: TriageToolCall[];
  mode: TriageMode;
  agent_error: string | null;
  /** Why the agent did not run, when mode is "rules_only". Distinguishing these
   *  matters: the inbox used to say "Agent disabled" for check-ins where the
   *  agent was enabled and the carer simply wrote no note — a screen asserting a
   *  configuration that was not the real configuration. */
  skipped_because?: "no_free_text" | "disabled" | null;
}

export interface Escalation {
  check_in_id: number;
  patient_id: number;
  patient_ref: string | null;
  completed_at: string;
  reason: string | null;
  urgency: Urgency;
  responses: Record<string, unknown> | null;
  triage: TriageRecord | null;
}

export const URGENCY_STYLES: Record<Urgency, { label: string; className: string }> = {
  urgent:  { label: "Urgent",  className: "border-signal/50 text-signal" },
  soon:    { label: "Soon",    className: "border-amber/40 text-amber" },
  routine: { label: "Routine", className: "border-raised text-muted" },
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
