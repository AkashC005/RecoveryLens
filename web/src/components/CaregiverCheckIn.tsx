/**
 * RecoveryLens — CaregiverCheckIn
 *
 * The carer-facing check-in form. This is the surface that makes `free_text`
 * mean something: until the triage agent existed, that field had no UI at all
 * and nothing read it.
 *
 * Written for someone who is tired, worried, and not medically trained. So:
 * plain questions, no jargon, no scales, and a free-text box that is explicitly
 * invited rather than buried as an optional extra. The free text is where the
 * information the three checkboxes cannot capture actually lives.
 *
 * What this screen must never do is imply the carer's message has been
 * clinically assessed. On submit it reports that a clinician will review it —
 * never a reassurance that things are fine, because neither the rules nor the
 * agent are capable of establishing that.
 */

import { useEffect, useState } from "react";
import { api, URGENCY_STYLES } from "../lib/api";
import type { CheckInResult, DueCheckIn } from "../lib/api";
import VoiceNote from "./VoiceNote";

/** The check-in that is actually next. Ordering by id would be ordering by when
 *  the row was written, which is not the same thing after a re-assessment. */
function earliest(rows: DueCheckIn[]): DueCheckIn {
  return [...rows].sort(
    (a, b) => +new Date(a.scheduled_for) - +new Date(b.scheduled_for))[0];
}

function Toggle({ label, hint, value, onChange }: {
  label: string;
  hint?: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="card p-4">
      <p className="text-base text-ink">{label}</p>
      {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      <div className="mt-3 flex gap-2">
        {[true, false].map((v) => (
          <button
            key={String(v)}
            type="button"
            onClick={() => onChange(v)}
            aria-pressed={value === v}
            className={`rounded-md border px-4 py-2 text-sm transition-colors ${
              value === v
                ? "border-accent-soft bg-surface text-ink"
                : "border-line text-muted hover:text-ink"
            }`}
          >
            {v ? "Yes" : "No"}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function CaregiverCheckIn({
  carerToken,
}: {
  /** Present when a carer arrived from their own link. They see exactly one
   *  check-in — theirs — because `/api/checkins/due` is clinician-only and
   *  handing a carer every due check-in in the hospital would be the leak the
   *  whole access-control change exists to close. */
  carerToken?: string | null;
} = {}) {
  const [due, setDue] = useState<DueCheckIn[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [taking, setTaking] = useState(true);
  const [symptoms, setSymptoms] = useState(false);
  const [worse, setWorse] = useState(false);
  const [freeText, setFreeText] = useState("");
  const [result, setResult] = useState<CheckInResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Ask for due check-ins first. If none have come round yet — which is the
  // normal state for the first three days after an assessment — fall back to
  // showing scheduled ones so the screen is usable and testable. The banner
  // below makes clear which case you are looking at.
  const [showingScheduled, setShowingScheduled] = useState(false);

  // ONE row per patient: the soonest check-in that is still open.
  //
  // The list used to show every pending check-in, so a single patient with a
  // seven-date follow-up plan filled the picker with seven near-identical rows
  // reading "#4 · due 12/08/2026". That labels the row by our database id and
  // the date, neither of which tells anyone WHO it is about — and nobody answers
  // a check-in for day 42 while day 3 is still open, so the other six were noise.
  const nextPerPatient = (() => {
    const soonest = new Map<number, DueCheckIn>();
    for (const c of due ?? []) {
      const held = soonest.get(c.patient_id);
      if (!held || new Date(c.scheduled_for) < new Date(held.scheduled_for)) {
        soonest.set(c.patient_id, c);
      }
    }
    return [...soonest.values()].sort(
      (a, b) => +new Date(a.scheduled_for) - +new Date(b.scheduled_for));
  })();

  useEffect(() => {
    if (carerToken) {
      api.checkInByToken(carerToken)
        .then((c) => {
          setDue([c]);
          setSelected(c.id);
        })
        .catch((e) => setError(String(e)));
      return;
    }

    api.dueCheckIns()
      .then(async (rows) => {
        if (rows.length === 0) {
          const upcoming = await api.dueCheckIns(true);
          setShowingScheduled(upcoming.length > 0);
          setDue(upcoming);
          if (upcoming.length) setSelected(earliest(upcoming).id);
          return;
        }
        setDue(rows);
        setSelected(earliest(rows).id);
      })
      .catch((e) => setError(String(e)));
  }, [carerToken]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (selected == null || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.submitCheckIn(selected, {
        taking_medication: taking,
        new_symptoms: symptoms,
        worse_than_last_week: worse,
        free_text: freeText.trim() || null,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    const u = URGENCY_STYLES[result.urgency];
    return (
      <div className="card p-6" aria-live="polite">
        <p className="text-base text-ink">{result.message}</p>

        {result.escalated && (
          <div className="mt-4">
            <span className={u.className}>
              {u.label}
            </span>
            <p className="mt-3 text-sm text-muted">
              Flagged because: {result.escalation_reason}
            </p>
            {result.agent_reasons.length > 0 && (
              <p className="mt-2 text-xs text-muted">
                Part of this came from what you wrote, not just the questions above.
              </p>
            )}
          </div>
        )}

        <button
          onClick={() => { setResult(null); setFreeText(""); }}
          className="mt-6 text-sm text-accent"
        >
          Submit another check-in
        </button>
      </div>
    );
  }

  if (error && !due) return <p className="text-sm text-danger">{error}</p>;
  if (!due) return <p className="text-sm text-muted">Loading…</p>;
  if (due.length === 0)
    return (
      <div className="card p-8 text-center">
        <p className="text-base text-ink">No check-ins due</p>
        <p className="mt-1 text-sm text-muted">
          Check-ins appear here when they are scheduled.
        </p>
      </div>
    );

  return (
    <form onSubmit={submit} className="space-y-4">
      {showingScheduled && !carerToken && (
        <div className="card p-3">
          <p className="text-xs text-muted">
            None of these check-ins is due yet — the first is scheduled for day 3
            after discharge. Showing upcoming ones so the form can be used and
            tested. In normal operation a carer only sees a check-in once its
            date arrives.
          </p>
        </div>
      )}

      {nextPerPatient.length > 1 && !carerToken && (
        <div className="card p-4">
          <label className="field-label" htmlFor="checkin">Who is this about?</label>
          <select
            id="checkin"
            className="field-input"
            value={selected ?? ""}
            onChange={(e) => setSelected(Number(e.target.value))}
          >
            {nextPerPatient.map((d) => (
              <option key={d.id} value={d.id}>
                {d.patient_ref || `Patient ${d.patient_id}`} — due{" "}
                {new Date(d.scheduled_for).toLocaleDateString(undefined, {
                  day: "numeric", month: "short",
                })}
              </option>
            ))}
          </select>
        </div>
      )}

      <Toggle
        label="Are they taking their medicines?"
        hint="As prescribed, most days."
        value={taking}
        onChange={setTaking}
      />
      <Toggle
        label="Anything new since the last check-in?"
        hint="New symptoms, or something that wasn't happening before."
        value={symptoms}
        onChange={setSymptoms}
      />
      <Toggle
        label="Are they worse than last week?"
        value={worse}
        onChange={setWorse}
      />

      {/* The field the whole triage agent exists to read. */}
      <div className="card p-4">
        <label className="field-label" htmlFor="notes">
          Anything else you've noticed?
        </label>
        <p className="mb-2 text-sm text-muted">
          In your own words. Small changes are worth mentioning — how they seem in
          themselves, sleep, mood, appetite, anything that feels different.
        </p>
        <textarea
          id="notes"
          rows={4}
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          placeholder="e.g. He's been more confused since Tuesday and hasn't wanted to eat much."
          className="field-input resize-y"
        />
      </div>

      {/* A confirmed transcript lands in the box above rather than being sent
          separately. One field, one submission, one path to the triage agent —
          so a spoken note and a typed one are read identically, and there is no
          second way to answer a check-in. The carer can also edit it afterwards,
          which is the point of putting it somewhere visible. */}
      <VoiceNote
        checkInId={selected}
        disabled={busy}
        onConfirmed={(text) =>
          setFreeText((existing) =>
            existing.trim() ? `${existing.trim()}\n\n${text}` : text)
        }
      />

      {error && <p className="text-sm text-danger">{error}</p>}

      <button
        type="submit"
        disabled={busy || selected == null}
        className="btn-primary"
      >
        {busy ? "Sending…" : "Send check-in"}
      </button>

      <p className="max-w-prose text-xs text-faint">
        This is not a way to get urgent help. If you are worried about them right
        now, contact your doctor or emergency services.
      </p>
    </form>
  );
}
