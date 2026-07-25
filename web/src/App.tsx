/**
 * RecoveryLens — App shell
 */

import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import AssessmentForm from "./components/AssessmentForm";
import RiskTimeline from "./components/RiskTimeline";
import type { AssessmentResponse, PatientSummary } from "./lib/api";
import { api, TIER_STYLES } from "./lib/api";

type View = "assess" | "result" | "patients";

function Header({ view, setView, online }: {
  view: View;
  setView: (v: View) => void;
  online: boolean | null;
}) {
  const tabs: { id: View; label: string }[] = [
    { id: "assess", label: "New assessment" },
    { id: "patients", label: "Patients" },
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
                view === t.id || (view === "result" && t.id === "assess")
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

function PatientList() {
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
        <li key={p.id} className="card flex flex-wrap items-center gap-4 p-4">
          <div className="min-w-0 flex-1">
            <p className="text-base text-bone">
              {p.patient_ref || <span className="text-muted">Unlabelled</span>}
            </p>
            <p className="font-mono text-xs text-muted">
              #{p.id} · {new Date(p.created_at).toLocaleDateString()}
            </p>
          </div>
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
        </li>
      ))}
    </ul>
  );
}

export default function App() {
  const [view, setView] = useState<View>("assess");
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const reduce = useReducedMotion();

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
              <h2 className="mb-6 text-xl font-semibold text-bone">Patients</h2>
              <PatientList />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}
