/**
 * RecoveryLens — App shell
 */

import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import AssessmentForm from "./components/AssessmentForm";
import RiskTimeline from "./components/RiskTimeline";
import CaregiverCheckIn from "./components/CaregiverCheckIn";
import ClinicianInbox from "./components/ClinicianInbox";
import PatientDetail from "./components/PatientDetail";
import type { AssessmentResponse, PatientSummary } from "./lib/api";
import { api, TIER_STYLES } from "./lib/api";

type View = "assess" | "result" | "patients" | "patient" | "checkin" | "inbox";

function Header({ view, setView, online }: {
  view: View;
  setView: (v: View) => void;
  online: boolean | null;
}) {
  const tabs: { id: View; label: string }[] = [
    { id: "assess", label: "New assessment" },
    { id: "patients", label: "Patients" },
    { id: "checkin", label: "Check-in" },
    { id: "inbox", label: "Review" },
  ];
  return (
    <header className="border-b border-raised">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-bone">RecoveryLens</h1>
          <p className="text-xs text-muted">Post-stroke risk and follow-up</p>
        </div>
        <nav className="flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setView(t.id)}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                view === t.id ||
                (view === "result" && t.id === "assess") ||
                (view === "patient" && t.id === "patients")
                  ? "bg-slate text-bone"
                  : "text-muted hover:text-bone"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <span className="ml-auto flex items-center gap-2 text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full ${
              online === null ? "bg-muted" : online ? "bg-teal" : "bg-signal"
            }`}
            aria-hidden
          />
          {online === null ? "Connecting…" : online ? "API connected" : "API offline"}
        </span>
      </div>
    </header>
  );
}

function PatientList({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<PatientSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.patients().then(setRows).catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="text-sm text-signal">{error}</p>;
  if (!rows) return <p className="text-sm text-muted">Loading…</p>;
  if (rows.length === 0)
    return (
      <div className="card p-8 text-center">
        <p className="text-base text-bone">No patients assessed yet</p>
        <p className="mt-1 text-sm text-muted">
          Complete an assessment and it will appear here.
        </p>
      </div>
    );

  return (
    <ul className="space-y-3">
      {rows.map((p) => (
        <li key={p.id}>
          {/* The whole row is the control. Previously nothing here was
              clickable, so GET /api/patients/{id} was served and unreachable. */}
          <button
            type="button"
            onClick={() => onOpen(p.id)}
            className="card flex w-full flex-wrap items-center gap-4 p-4 text-left transition-colors hover:border-teal-dim"
          >
            <div className="min-w-0 flex-1">
              <p className="text-base text-bone">
                {p.patient_ref || <span className="text-muted">Unlabelled</span>}
              </p>
              <p className="font-mono text-xs text-muted">
                #{p.id} · {new Date(p.created_at).toLocaleDateString()}
                {p.next_check_in && (
                  <> · next {new Date(p.next_check_in).toLocaleDateString()}</>
                )}
              </p>
            </div>

            {/* Two states that must not look like a healthy row: a carer who
                opted out, and one whose consent was never recorded. Both mean
                follow-up has silently stopped. */}
            {p.opted_out ? (
              <span className="rounded-full border border-signal/50 px-2 py-0.5 text-[11px] text-signal">
                Opted out
              </span>
            ) : !p.consent_recorded ? (
              <span className="rounded-full border border-amber/40 px-2 py-0.5 text-[11px] text-amber">
                No consent
              </span>
            ) : null}

            {p.open_escalations > 0 && (
              <span className="rounded-full border border-signal/50 px-2 py-0.5 text-[11px] text-signal">
                {p.open_escalations} flagged
              </span>
            )}

            {p.latest_tier_summary && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(p.latest_tier_summary).map(([outcome, tier]) => (
                  <span
                    key={outcome}
                    title={`${outcome}: ${tier}`}
                    className={`h-2.5 w-2.5 rounded-full ${
                      TIER_STYLES[tier as keyof typeof TIER_STYLES]?.dot ?? "bg-muted"
                    }`}
                  />
                ))}
              </div>
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function App() {
  const [view, setView] = useState<View>("assess");
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [patientId, setPatientId] = useState<number | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const reduce = useReducedMotion();

  const openPatient = (id: number) => {
    setPatientId(id);
    setView("patient");
  };

  useEffect(() => {
    api.health().then(() => setOnline(true)).catch(() => setOnline(false));
  }, []);

  const fade = reduce
    ? {}
    : {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -8 },
        transition: { duration: 0.2 },
      };

  return (
    <div className="min-h-screen bg-ink">
      <Header view={view} setView={setView} online={online} />

      <main className="mx-auto max-w-5xl px-5 py-8">
        <AnimatePresence mode="wait">
          {view === "assess" && (
            <motion.div key="assess" {...fade}>
              <h2 className="text-xl font-semibold text-bone">Discharge assessment</h2>
              <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
                Record the patient as they are at discharge. All fields reflect
                information available before leaving hospital.
              </p>
              <AssessmentForm
                onResult={(r) => {
                  setResult(r);
                  setView("result");
                }}
              />
            </motion.div>
          )}

          {view === "result" && result && (
            <motion.div key="result" {...fade}>
              <button
                onClick={() => setView("assess")}
                className="mb-6 text-sm text-teal hover:text-bone"
              >
                ← New assessment
              </button>
              <RiskTimeline result={result} />
            </motion.div>
          )}

          {view === "patients" && (
            <motion.div key="patients" {...fade}>
              <h2 className="text-xl font-semibold text-bone">Patients</h2>
              <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
                Everyone assessed so far. Select a row for the full record —
                risk profile, every check-in and its triage reasoning, and
                whether follow-up messages are actually reaching the carer.
              </p>
              <PatientList onOpen={openPatient} />
            </motion.div>
          )}

          {view === "patient" && patientId !== null && (
            <motion.div key={`patient-${patientId}`} {...fade}>
              <PatientDetail
                patientId={patientId}
                onBack={() => setView("patients")}
              />
            </motion.div>
          )}

          {view === "checkin" && (
            <motion.div key="checkin" {...fade}>
              <h2 className="text-xl font-semibold text-bone">Check-in</h2>
              <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
                For the person looking after them at home. A few short questions,
                and space to say anything else you&rsquo;ve noticed.
              </p>
              <CaregiverCheckIn />
            </motion.div>
          )}

          {view === "inbox" && (
            <motion.div key="inbox" {...fade}>
              <h2 className="text-xl font-semibold text-bone">Awaiting review</h2>
              <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
                Check-ins that were escalated, most urgent first. Each shows
                whether the flag came from the rule checks or the triage agent,
                and what the agent looked at before deciding.
              </p>
              <ClinicianInbox onOpenPatient={openPatient} />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}
