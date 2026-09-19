/**
 * RecoveryLens — PrescriptionImport
 *
 * Read a prescription, check every row against the paper, then schedule the
 * carer's reminders.
 *
 * WHY THE CONFIRM STEP IS NOT SKIPPABLE
 * -------------------------------------
 * Everything else this product reads is a risk input: a wrong stroke subtype
 * moves a rank and the clinician sees the drivers. A dose is different. If a
 * "1" is read into the wrong column, a family is told to give a tablet at a
 * time nobody prescribed, and the message looks exactly as authoritative as a
 * correct one. So nothing here is saved, and no message is scheduled, until a
 * named clinician has submitted the rows from this screen.
 *
 * WHY THE SOURCE IS ALWAYS ON SCREEN
 * ----------------------------------
 * A PDF with a text layer is parsed from column geometry — deterministic, and
 * a dose belongs to a time of day because of where it physically sits. A
 * photograph has no geometry, so a model reads it, and the same photo can parse
 * differently twice. Those two are not equally trustworthy and the screen must
 * not let them look alike.
 */

import { useRef, useState } from "react";
import { api } from "../lib/api";
import type { ConfirmedPrescription, Medication, ParsedPrescription } from "../lib/api";

const SLOTS = ["morning", "noon", "evening", "night"] as const;
type Slot = (typeof SLOTS)[number];

const ACCEPT = ".pdf,image/png,image/jpeg,image/webp";

function blankRow(): Medication {
  return { name: "", qty: "", take: "", morning: "", noon: "", evening: "",
           night: "", days: null, as_needed: false };
}

/** A row is only worth reminding about if it has a time and an end date. */
function schedulable(m: Medication): boolean {
  return !m.as_needed && !!m.days && SLOTS.some((s) => (m[s] ?? "") !== "");
}

export default function PrescriptionImport({
  patientId,
  onConfirmed,
}: {
  patientId: number;
  onConfirmed: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [parsed, setParsed] = useState<ParsedPrescription | null>(null);
  const [rows, setRows] = useState<Medication[]>([]);
  const [nextVisit, setNextVisit] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ConfirmedPrescription | null>(null);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const type = file.type || (file.name.endsWith(".pdf") ? "application/pdf" : "");
      const result = await api.parsePrescription(await file.arrayBuffer(), type);
      setParsed(result);
      setRows(result.medications.map((m) => ({ ...m })));
      setNextVisit(result.next_visit ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function set<K extends keyof Medication>(i: number, key: K, value: Medication[K]) {
    setRows((prev) => prev.map((r, n) => (n === i ? { ...r, [key]: value } : r)));
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const payload = rows
        .filter((r) => (r.name ?? "").trim() !== "")
        .map((r) => ({
          name: r.name.trim(),
          qty: r.qty || null, take: r.take || null,
          morning: r.morning || null, noon: r.noon || null,
          evening: r.evening || null, night: r.night || null,
          days: r.days ?? null, as_needed: !!r.as_needed,
        }));
      const result = await api.confirmPrescription(patientId, {
        medications: payload,
        diagnosis: parsed?.diagnosis ?? null,
        next_visit: nextVisit || null,
        source: parsed?.source ?? "pdf_geometry",
      });
      setDone(result);
      setParsed(null);
      setRows([]);
      onConfirmed();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const fromPhoto = parsed?.source === "image_transcription";
  const willSchedule = rows.filter(schedulable);

  return (
    <section className="card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink">Prescription</h3>
          <p className="mt-0.5 text-sm text-muted">
            Read the medicines off the printout, then send the carer a daily
            check that they are being taken.
          </p>
        </div>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          className="btn-secondary"
        >
          {busy ? "Reading…" : "Upload a prescription"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
          }}
        />
      </div>

      <p className="mt-2 text-xs text-muted">
        A PDF is read from the column positions on the page, so the same file
        gives the same answer every time. A photograph is read by a model
        instead and needs checking line by line.
      </p>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {done && (
        <div className="mt-4 rounded-lg border border-calm/25 bg-calm-soft p-4">
          <p className="text-sm font-medium text-calm">
            Confirmed by {done.confirmed_by}. {done.scheduled_checkins} daily
            check{done.scheduled_checkins === 1 ? "" : "s"} scheduled over{" "}
            {done.course_days} days.
          </p>
          {done.not_scheduled.length > 0 && (
            <p className="mt-1.5 text-xs text-ink-soft">
              No reminder for {done.not_scheduled.join(", ")} — as-needed, or no
              dose times and course length to build one from.
            </p>
          )}
        </div>
      )}

      {parsed?.error && (
        <p className="mt-3 text-sm text-danger">{parsed.error}</p>
      )}

      {parsed && rows.length > 0 && (
        <div className="mt-4">
          {/* Provenance, before the table rather than under it. */}
          <div
            className={`rounded-lg border p-3 ${
              fromPhoto
                ? "border-warn/25 bg-warn-soft"
                : "border-accent-soft bg-accent-wash"
            }`}
          >
            <p className={`text-sm font-medium ${fromPhoto ? "text-warn" : "text-accent-strong"}`}>
              {fromPhoto
                ? "Read from a photograph by a model"
                : "Measured from the PDF’s column positions"}
            </p>
            <p className="mt-1 text-xs text-ink-soft">
              {fromPhoto
                ? "This path cannot verify which column a number came from. Check every dose against the paper before confirming."
                : "Deterministic — a dose belongs to a time of day because of where it sits on the page. Still check it against the paper."}
            </p>
          </div>

          {parsed.warnings.map((w) => (
            <p key={w} className="mt-2 text-xs text-warn">{w}</p>
          ))}

          {parsed.rejected.length > 0 && (
            <div className="mt-2 rounded-lg border border-line bg-sunken p-3">
              <p className="section-label">Refused — type these in yourself</p>
              <ul className="mt-1.5 space-y-1">
                {parsed.rejected.map((r, i) => (
                  <li key={i} className="text-xs text-ink-soft">
                    <span className="font-medium">{r.medicine ?? r.field}</span>{" "}
                    — {r.field} read as “{r.value}”: {r.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[46rem] text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  {["Medicine", "Qty", "Take", "Morn", "Noon", "Eve", "Night", "Days", ""]
                    .map((h) => (
                      <th key={h} className="pb-2 pr-2 section-label font-semibold">{h}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((m, i) => (
                  <tr key={i} className="border-b border-line-soft">
                    <td className="py-1.5 pr-2">
                      <input
                        className="field-input py-1 text-sm"
                        value={m.name}
                        onChange={(e) => set(i, "name", e.target.value)}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        className="field-input w-16 py-1 text-sm"
                        value={m.qty ?? ""}
                        onChange={(e) => set(i, "qty", e.target.value)}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        className="field-input w-28 py-1 text-sm"
                        value={m.take ?? ""}
                        onChange={(e) => set(i, "take", e.target.value)}
                      />
                    </td>
                    {SLOTS.map((slot: Slot) => (
                      <td key={slot} className="py-1.5 pr-2">
                        <input
                          className="field-input w-14 py-1 text-center text-sm"
                          value={m[slot] ?? ""}
                          placeholder="—"
                          onChange={(e) => set(i, slot, e.target.value)}
                        />
                      </td>
                    ))}
                    <td className="py-1.5 pr-2">
                      <input
                        className="field-input w-16 py-1 text-center text-sm"
                        value={m.days ?? ""}
                        onChange={(e) =>
                          set(i, "days", e.target.value ? Number(e.target.value) : null)}
                      />
                    </td>
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        onClick={() => setRows((p) => p.filter((_, n) => n !== i))}
                        className="text-xs text-muted hover:text-danger"
                        aria-label={`Remove ${m.name || "row"}`}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={() => setRows((p) => [...p, blankRow()])}
              className="text-sm text-accent"
            >
              Add a medicine
            </button>
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              Next visit
              <input
                type="date"
                className="field-input w-40 py-1 text-sm"
                value={nextVisit}
                onChange={(e) => setNextVisit(e.target.value)}
              />
            </label>
          </div>

          {/* Said before the button: what will and will not be sent. */}
          <div className="mt-4 rounded-lg border border-line bg-sunken p-3">
            <p className="text-sm text-ink">
              {willSchedule.length === 0
                ? "Nothing here can be reminded about — a reminder needs both dose times and a course length."
                : `${willSchedule.length} medicine${willSchedule.length === 1 ? "" : "s"} will be checked daily for ${Math.max(
                    ...willSchedule.map((m) => m.days ?? 0))} days.`}
            </p>
            {rows.some((r) => r.name.trim() && !schedulable(r)) && (
              <p className="mt-1 text-xs text-muted">
                No reminder for{" "}
                {rows.filter((r) => r.name.trim() && !schedulable(r))
                     .map((r) => r.name).join(", ")}
                . They stay on the record.
              </p>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={confirm}
              disabled={busy || rows.every((r) => !r.name.trim())}
              className="btn-primary"
            >
              {busy ? "Saving…" : "I have checked these against the paper"}
            </button>
            <button
              type="button"
              onClick={() => { setParsed(null); setRows([]); }}
              className="btn-secondary"
            >
              Discard
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
