/**
 * RecoveryLens — AssessmentForm
 *
 * Fields are grouped the way a clinician works through a patient at discharge:
 * who they are, how they presented, what deficits they have, what history and
 * imaging show, what treatment is planned.
 *
 * The deficit control offers three states, not a checkbox. "Cannot assess" is a
 * real clinical answer and carries genuine prognostic weight (14-day mortality
 * among unassessable visual fields is 21.3%, higher than when the deficit is
 * present). Forcing it into yes/no would discard that.
 */

import { useState } from "react";
import type {
  AssessmentRequest,
  AssessmentResponse,
  DeficitState,
} from "../lib/api";
import { api } from "../lib/api";
import DischargeSummaryImport from "./DischargeSummaryImport";

const DEFICITS: { key: keyof AssessmentRequest; label: string }[] = [
  { key: "deficit_face", label: "Facial weakness" },
  { key: "deficit_arm", label: "Arm or hand weakness" },
  { key: "deficit_leg", label: "Leg or foot weakness" },
  { key: "deficit_speech", label: "Difficulty speaking" },
  { key: "deficit_visual_field", label: "Visual field loss" },
  { key: "deficit_visuospatial", label: "Visuospatial difficulty" },
  { key: "deficit_brainstem", label: "Brainstem / cerebellar signs" },
  { key: "deficit_other", label: "Other deficit" },
];

const DEFICIT_OPTIONS: { value: DeficitState; label: string }[] = [
  { value: "absent", label: "Absent" },
  { value: "present", label: "Present" },
  { value: "cannot_assess", label: "Can't assess" },
];

const EMPTY: AssessmentRequest = {
  patient_ref: "",
  age: 70,
  sex: "F",
  hours_since_onset: 6,
  consciousness: "alert",
  systolic_bp: 150,
  stroke_subtype: "PACS",
  symptoms_on_waking: false,
  deficit_face: "absent",
  deficit_arm: "absent",
  deficit_leg: "absent",
  deficit_speech: "absent",
  deficit_visual_field: "absent",
  deficit_visuospatial: "absent",
  deficit_brainstem: "absent",
  deficit_other: "absent",
  atrial_fibrillation: "unknown",
  ct_before_treatment: false,
  infarct_visible_on_ct: false,
  heparin_last_24h: false,
  aspirin_last_3days: false,
  planned_aspirin: false,
  planned_heparin: "none",
  caregiver_contact: "",
  caregiver_language: "en",
};

function Section({ title, help, children }: {
  title: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="card p-5">
      <legend className="px-2 text-sm font-semibold uppercase tracking-wide text-teal">
        {title}
      </legend>
      {help && <p className="text-sm text-muted mb-4 max-w-prose">{help}</p>}
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

function Toggle({ label, checked, onChange }: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-3 cursor-pointer py-1">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-raised bg-slate accent-teal"
      />
      <span className="text-sm text-bone">{label}</span>
    </label>
  );
}

export default function AssessmentForm({
  onResult,
}: {
  onResult: (r: AssessmentResponse) => void;
}) {
  const [form, setForm] = useState<AssessmentRequest>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fields a model filled in that the clinician has not touched yet, and the
  // sentence each came from. A field leaves this set the moment it is edited —
  // editing IS reviewing, so re-marking it would be nagging.
  const [machineFilled, setMachineFilled] =
    useState<Record<string, string>>({});

  function set<K extends keyof AssessmentRequest>(key: K, value: AssessmentRequest[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setMachineFilled((m) => {
      if (!(key in m)) return m;
      const next = { ...m };
      delete next[key as string];
      return next;
    });
  }

  /** Marks a field the clinician still needs to look at, and carries the quote
   *  as a tooltip. Extraction is the first place a model writes into a CLINICAL
   *  INPUT rather than a display surface — everything downstream is computed
   *  from these values — so an unreviewed one must not look like a typed one. */
  function extractedProps(key: keyof AssessmentRequest) {
    const source = machineFilled[key as string];
    // ALWAYS returns className. Returning {} when a field is not extracted left
    // the input with no class at all and stripped its styling — this helper
    // replaces the className rather than adding to it.
    return {
      className: source ? "field-input ring-1 ring-amber/50" : "field-input",
      ...(source
        ? { "data-extracted": "true",
            title: `Read from the summary: "${source}"` }
        : {}),
    };
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = {
        ...form,
        patient_ref: (form.patient_ref ?? "").trim(),
        caregiver_contact: form.caregiver_contact || null,
        caregiver_language: form.caregiver_language || "en",
      };
      onResult(await api.assess(payload));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <DischargeSummaryImport
        onExtracted={(values, result) => {
          setForm((f) => ({ ...f, ...values }));
          setMachineFilled(
            Object.fromEntries(result.fields.map((x) => [x.name, x.source])));
        }}
      />

      {Object.keys(machineFilled).length > 0 && (
        <div className="card border border-amber/40 p-3">
          <p className="text-sm text-amber">
            {Object.keys(machineFilled).length} field
            {Object.keys(machineFilled).length === 1 ? "" : "s"} filled from the
            summary and not yet checked.
          </p>
          <p className="mt-1 text-xs text-muted">
            They are outlined below — hover any one to see the sentence it came
            from. Editing a field marks it as checked. Nothing is submitted until
            you press the button at the bottom.
          </p>
        </div>
      )}

      <Section title="Patient">
        <div>
          <label className="field-label" htmlFor="ref">Patient reference</label>
          <input
            id="ref"
            className="field-input"
            placeholder="e.g. ward3-014"
            value={form.patient_ref ?? ""}
            onChange={(e) => set("patient_ref", e.target.value)}
            required
            maxLength={64}
          />
          {/* Required, because this is how the patient appears in every list, every
              escalation and every carer link. An unlabelled patient is one a
              clinician cannot act on. Still not a real name — see below. */}
          <p className="mt-1 text-xs text-muted">
            How this patient appears everywhere in the app. A ward reference or
            study number — <strong className="text-amber/90">not a real
            name</strong>, since this database holds no clinical governance.
          </p>
        </div>
        <div>
          <label className="field-label" htmlFor="age">Age (years)</label>
          <input
            id="age" type="number" min={16} max={110} required
            value={form.age}
            {...extractedProps("age")}
            onChange={(e) => set("age", Number(e.target.value))}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="sex">Sex</label>
          <select
            id="sex" value={form.sex} {...extractedProps("sex")}
            onChange={(e) => set("sex", e.target.value as AssessmentRequest["sex"])}
          >
            <option value="F">Female</option>
            <option value="M">Male</option>
          </select>
        </div>
      </Section>

      <Section title="Presentation">
        <div>
          <label className="field-label" htmlFor="onset">Hours since onset</label>
          <input
            id="onset" type="number" min={0} max={48} step={0.5} required
            value={form.hours_since_onset}
            {...extractedProps("hours_since_onset")}
            onChange={(e) => set("hours_since_onset", Number(e.target.value))}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="consc">Level of consciousness</label>
          <select
            id="consc" value={form.consciousness}
            {...extractedProps("consciousness")}
            onChange={(e) => set("consciousness", e.target.value as AssessmentRequest["consciousness"])}
          >
            <option value="alert">Fully alert</option>
            <option value="drowsy">Drowsy</option>
            <option value="unconscious">Unconscious</option>
          </select>
        </div>
        <div>
          <label className="field-label" htmlFor="sbp">Systolic BP (mmHg)</label>
          <input
            id="sbp" type="number" min={60} max={300} required
            value={form.systolic_bp}
            {...extractedProps("systolic_bp")}
            onChange={(e) => set("systolic_bp", Number(e.target.value))}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="stype">Stroke subtype (Bamford)</label>
          <select
            id="stype" value={form.stroke_subtype}
            {...extractedProps("stroke_subtype")}
            onChange={(e) => set("stroke_subtype", e.target.value as AssessmentRequest["stroke_subtype"])}
          >
            <option value="TACS">TACS — total anterior</option>
            <option value="PACS">PACS — partial anterior</option>
            <option value="LACS">LACS — lacunar</option>
            <option value="POCS">POCS — posterior</option>
            <option value="OTH">Other / unclassified</option>
          </select>
        </div>
        <Toggle
          label="Symptoms noticed on waking"
          checked={form.symptoms_on_waking}
          onChange={(v) => set("symptoms_on_waking", v)}
        />
      </Section>

      <Section
        title="Neurological deficits"
        help="“Can't assess” is recorded as its own value — it carries real prognostic weight and is not treated as missing data."
      >
        {DEFICITS.map(({ key, label }) => (
          <div key={key as string}>
            <span className="field-label">{label}</span>
            <div role="radiogroup" aria-label={label} className="flex gap-1">
              {DEFICIT_OPTIONS.map((opt) => {
                const active = form[key] === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => set(key, opt.value as never)}
                    className={`flex-1 rounded-md border px-2 py-1.5 text-xs transition-colors ${
                      active
                        ? "border-teal bg-teal-dim/25 text-bone"
                        : "border-raised bg-slate text-muted hover:border-teal-dim"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </Section>

      <Section title="History and imaging">
        <div>
          <label className="field-label" htmlFor="af">Atrial fibrillation</label>
          <select
            id="af" className="field-input" value={form.atrial_fibrillation}
            onChange={(e) => set("atrial_fibrillation", e.target.value as AssessmentRequest["atrial_fibrillation"])}
          >
            <option value="unknown">Unknown</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </div>
        <div className="space-y-1">
          <Toggle label="CT before treatment" checked={form.ct_before_treatment}
                  onChange={(v) => set("ct_before_treatment", v)} />
          <Toggle label="Infarct visible on CT" checked={form.infarct_visible_on_ct}
                  onChange={(v) => set("infarct_visible_on_ct", v)} />
          <Toggle label="Heparin in preceding 24h" checked={form.heparin_last_24h}
                  onChange={(v) => set("heparin_last_24h", v)} />
          <Toggle label="Aspirin in preceding 3 days" checked={form.aspirin_last_3days}
                  onChange={(v) => set("aspirin_last_3days", v)} />
        </div>
      </Section>

      <Section title="Planned treatment and contact">
        <div>
          <label className="field-label" htmlFor="hep">Heparin planned</label>
          <select
            id="hep" className="field-input" value={form.planned_heparin}
            onChange={(e) => set("planned_heparin", e.target.value as AssessmentRequest["planned_heparin"])}
          >
            <option value="none">None</option>
            <option value="low">Low dose</option>
            <option value="medium">Medium dose</option>
          </select>
          <div className="mt-2">
            <Toggle label="Aspirin planned" checked={form.planned_aspirin}
                    onChange={(v) => set("planned_aspirin", v)} />
          </div>
        </div>
        <div>
          <label className="field-label" htmlFor="contact">Caregiver contact (optional)</label>
          <input
            id="contact" className="field-input" placeholder="Phone or email"
            value={form.caregiver_contact ?? ""}
            onChange={(e) => set("caregiver_contact", e.target.value)}
          />
          <p className="mt-1 text-xs text-muted">
            Only with consent. Used for scheduled check-ins.
          </p>
        </div>
        <div>
          <label className="field-label" htmlFor="lang">Carer&rsquo;s language</label>
          <select
            id="lang" className="field-input"
            value={form.caregiver_language ?? "en"}
            onChange={(e) => set("caregiver_language",
                                 e.target.value as "en" | "ta" | "hi")}
          >
            <option value="en">English</option>
            <option value="ta">Tamil — தமிழ்</option>
            <option value="hi">Hindi — हिन्दी</option>
          </select>
          {/* Said here rather than buried in docs, because a clinician choosing
              Tamil should know what will and will not be translated before they
              rely on it. */}
          <p className="mt-1 text-xs text-muted">
            Applies to messages sent to the carer. Guideline quotations and
            everything on this side of the app stay in English — a translated
            recommendation is no longer a quotation.
          </p>
        </div>
      </Section>

      {error && (
        <p role="alert" className="text-sm text-signal">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-md bg-teal px-4 py-3 text-base font-medium text-ink
                   transition-colors hover:bg-teal-dim disabled:opacity-50
                   disabled:cursor-not-allowed sm:w-auto sm:px-8"
      >
        {busy ? "Assessing…" : "Assess patient"}
      </button>
    </form>
  );
}
