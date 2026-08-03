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

function Toggle({ label, hint, value, onChange }: {
  label: string;
  hint?: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="card p-4">
      <p className="text-base text-bone">{label}</p>
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
                ? "border-teal-dim bg-slate text-bone"
                : "border-raised text-muted hover:text-bone"
            }`}
          >
            {v ? "Yes" : "No"}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function CaregiverCheckIn() {
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

  useEffect(() => {
    api.dueCheckIns()
      .then(async (rows) => {
        if (rows.length === 0) {
          const upcoming = await api.dueCheckIns(true);
          setShowingScheduled(upcoming.length > 0);
          setDue(upcoming);
          if (upcoming.length) setSelected(upcoming[0].id);
          return;
        }
        setDue(rows);
        setSelected(rows[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

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
        <p className="text-base text-bone">{result.message}</p>

        {result.escalated && (
          <div className="mt-4">
            <span className={`rounded-full border px-2 py-0.5 text-xs ${u.className}`}>
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
          className="mt-6 text-sm text-teal"
        >
          Submit another check-in
        </button>
      </div>
    );
  }

  if (error && !due) return <p className="text-sm text-signal">{error}</p>;
  if (!due) return <p className="text-sm text-muted">Loading…</p>;
  if (due.length === 0)
    return (
      <div className="card p-8 text-center">
        <p className="text-base text-bone">No check-ins due</p>
        <p className="mt-1 text-sm text-muted">
          Check-ins appear here when they are scheduled.
        </p>
      </div>
    );

  return (
    <form onSubmit={submit} className="space-y-4">
      {showingScheduled && (
        <div className="card p-3">
          <p className="text-xs text-muted">
            None of these check-ins is due yet — the first is scheduled for day 3
            after discharge. Showing upcoming ones so the form can be used and
            tested. In normal operation a carer only sees a check-in once its
            date arrives.
          </p>
        </div>
      )}

      {due.length > 1 && (
        <div className="card p-4">
          <label className="field-label" htmlFor="checkin">Which check-in?</label>
          <select
            id="checkin"
            className="field-input"
            value={selected ?? ""}
            onChange={(e) => setSelected(Number(e.target.value))}
          >
            {due.map((d) => (
              <option key={d.id} value={d.id}>
                #{d.id} · due {new Date(d.scheduled_for).toLocaleDateString()}
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

      {error && <p className="text-sm text-signal">{error}</p>}

      <button
        type="submit"
        disabled={busy || selected == null}
        className="rounded-md border border-teal-dim px-5 py-2.5 text-sm text-teal
                   disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy ? "Sending…" : "Send check-in"}
      </button>

      <p className="max-w-prose text-xs text-muted/80">
        This is not a way to get urgent help. If you are worried about them right
        now, contact your doctor or emergency services.
      </p>
    </form>
  );
}
