/**
 * RecoveryLens — App shell
 */

import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import AssessmentForm from "./components/AssessmentForm";
import RiskTimeline from "./components/RiskTimeline";
import CaregiverCheckIn from "./components/CaregiverCheckIn";
import ClinicianInbox from "./components/ClinicianInbox";
import InviteColleague from "./components/InviteColleague";
import PatientDetail from "./components/PatientDetail";
import SignIn from "./components/SignIn";
import type {
  AssessmentRequest,
  AssessmentResponse,
  Me,
  PatientSummary,
} from "./lib/api";
import { api, NotSignedIn, setCheckInToken, TIER_STYLES } from "./lib/api";

type View = "assess" | "result" | "patients" | "patient" | "checkin" | "inbox";

function Header({ view, setView, online, me, onSignOut }: {
  view: View;
  setView: (v: View) => void;
  online: boolean | null;
  me: Me | null;
  onSignOut: () => void;
}) {
  const tabs: { id: View; label: string }[] = [
    { id: "assess", label: "New assessment" },
    { id: "patients", label: "Patients" },
    { id: "checkin", label: "Check-in" },
    { id: "inbox", label: "Review" },
  ];
  return (
    // Sticky with a translucent backdrop: on the patient detail and result
    // screens the content runs long, and losing the navigation on scroll is the
    // difference between two clicks and a scroll back to the top.
    <header className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-3">
        <div className="flex items-center gap-2.5">
          <span
            className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-xs font-bold text-white"
            aria-hidden
          >
            RL
          </span>
          <h1 className="text-base font-semibold tracking-tight text-ink">
            RecoveryLens
          </h1>
        </div>

        <nav className="flex gap-0.5 rounded-lg bg-sunken p-1">
          {tabs.map((t) => {
            const active =
              view === t.id ||
              (view === "result" && t.id === "assess") ||
              (view === "patient" && t.id === "patients");
            return (
              <button
                key={t.id}
                onClick={() => setView(t.id)}
                aria-current={active ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150 ${
                  active
                    ? "bg-canvas text-ink shadow-card"
                    : "text-muted hover:text-ink"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="flex items-center gap-2 text-xs text-muted">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                online === null ? "bg-faint" : online ? "bg-calm" : "bg-danger"
              }`}
              aria-hidden
            />
            {online === null ? "Connecting…" : online ? "API connected" : "API offline"}
          </span>
          {me && (
            <span className="flex items-center gap-3 text-xs text-muted">
              {/* The organisation, not just the user. Which patients this screen
                  can show is a property of the organisation, so it belongs where
                  it can be checked at a glance — and it is the natural place to
                  add someone to it, rather than a nav tab for a rare action. */}
              <InviteColleague me={me} />
              <button onClick={onSignOut} className="text-accent hover:text-ink">
                Sign out
              </button>
            </span>
          )}
        </div>
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

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!rows) return <p className="text-sm text-muted">Loading…</p>;
  if (rows.length === 0)
    return (
      <div className="card px-8 py-14 text-center">
        <div
          aria-hidden
          className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-accent-wash text-lg text-accent"
        >
          +
        </div>
        <p className="mt-4 text-lg font-medium text-ink">No patients yet</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-muted">
          Complete a discharge assessment and the patient appears here with
          their risk profile and follow-up schedule.
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
            className="card flex w-full flex-wrap items-center gap-4 p-4 text-left transition-all duration-150 hover:border-accent-soft hover:shadow-lift"
          >
            <div className="min-w-0 flex-1">
              <p className="text-base text-ink">
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
              <span className="chip-danger">Opted out</span>
            ) : !p.consent_recorded ? (
              <span className="chip-warn">No consent</span>
            ) : null}

            {p.open_escalations > 0 && (
              <span className="chip-danger">{p.open_escalations} flagged</span>
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
  const [me, setMe] = useState<Me | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  /** Set when the assessment form is updating an existing patient rather than
   *  creating one. Holds the id to submit against and the previous inputs to
   *  start from. */
  const [reassessing, setReassessing] =
    useState<{ id: number; initial: Partial<AssessmentRequest> } | null>(null);
  const reduce = useReducedMotion();

  /** Navigate, clearing any re-assessment in progress.
   *
   *  Used for every deliberate move by the user. Without this, clicking
   *  "New assessment" after opening a re-assessment would show a form that
   *  looks new and silently submits against the old patient's id. */
  function navigate(v: View) {
    setReassessing(null);
    setView(v);
  }

  // A carer arriving from their own link. Detected before anything asks for a
  // session, because a carer has no account and must never be shown a login form
  // — they would reasonably conclude the link was broken and stop answering.
  const carerToken = new URLSearchParams(window.location.search).get("token");
  const isCarer = Boolean(carerToken);
  if (isCarer) setCheckInToken(carerToken);

  const openPatient = (id: number) => {
    setPatientId(id);
    setView("patient");
  };

  useEffect(() => {
    api.health().then(() => setOnline(true)).catch(() => setOnline(false));
  }, []);

  useEffect(() => {
    if (isCarer) {
      setAuthChecked(true);
      setView("checkin");
      return;
    }
    // A 401 here is the normal first-visit state, not an error worth showing.
    api.me()
      .then(setMe)
      .catch((e) => {
        if (!(e instanceof NotSignedIn)) console.error(e);
      })
      .finally(() => setAuthChecked(true));
  }, [isCarer]);

  async function signOut() {
    try {
      await api.logout();
    } finally {
      // Cleared locally whatever the server said. A failed logout request must
      // not leave the screen looking signed in.
      setMe(null);
      setView("assess");
      setPatientId(null);
    }
  }

  const fade = reduce
    ? {}
    : {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -8 },
        transition: { duration: 0.2 },
      };

  // ---------------------------------------------------------------- gates
  if (!authChecked)
    return (
      <div className="min-h-screen bg-wash p-8">
        <p className="text-sm text-muted">Connecting…</p>
      </div>
    );

  // The carer's view: one check-in, theirs, and no navigation to anything else.
  // Rendered before the session gate, because a carer has no session and showing
  // them a login form would read as a broken link.
  if (isCarer)
    return (
      <div className="min-h-screen bg-wash">
        <header className="border-b border-line">
          <div className="mx-auto max-w-2xl px-5 py-4">
            <h1 className="text-lg font-semibold tracking-tight text-ink">
              RecoveryLens
            </h1>
            <p className="text-xs text-muted">Check-in</p>
          </div>
        </header>
        <main className="mx-auto max-w-2xl px-5 py-8">
          <h2 className="text-xl font-semibold text-ink">How are they doing?</h2>
          <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
            A few short questions, and space to say anything else you&rsquo;ve
            noticed. This link is just for this check-in.
          </p>
          <CaregiverCheckIn carerToken={carerToken} />
        </main>
      </div>
    );

  if (!me) {
    return (
      <div className="min-h-screen bg-wash">
        {/* No app header here. SignIn owns the whole viewport — it carries its
            own branding in the hero panel, and a second header above it would
            just be a smaller duplicate of the one already on screen. */}
        <SignIn onSignedIn={setMe} />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-wash">
      <Header view={view} setView={navigate} online={online} me={me}
              onSignOut={signOut} />

      <main className="mx-auto max-w-6xl px-6 py-10">
        <AnimatePresence mode="wait">
          {view === "assess" && (
            <motion.div key="assess" {...fade}>
              <h2 className="text-xl font-semibold text-ink">
                {reassessing ? "Re-assessment" : "Discharge assessment"}
              </h2>
              <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
                {reassessing
                  ? "Update this patient with what has changed since the last assessment."
                  : "Record the patient as they are at discharge. All fields reflect information available before leaving hospital."}
              </p>
              <AssessmentForm
                // Remounts when switching between a new assessment and a
                // re-assessment. `initial` seeds state at mount only, so without
                // this the form would keep whatever was on screen before.
                key={reassessing ? `reassess-${reassessing.id}` : "new"}
                patientId={reassessing?.id}
                initial={reassessing?.initial}
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
                className="mb-6 text-sm text-accent hover:text-ink"
              >
                ← New assessment
              </button>
              <RiskTimeline result={result} />
            </motion.div>
          )}

          {view === "patients" && (
            <motion.div key="patients" {...fade}>
              <h2 className="text-xl font-semibold text-ink">Patients</h2>
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
                onReassess={(initial) => {
                  setReassessing({ id: patientId, initial });
                  setView("assess");
                }}
              />
            </motion.div>
          )}

          {view === "checkin" && (
            <motion.div key="checkin" {...fade}>
              <h2 className="text-xl font-semibold text-ink">Check-in</h2>
              <p className="mt-1 mb-6 max-w-prose text-sm text-muted">
                For the person looking after them at home. A few short questions,
                and space to say anything else you&rsquo;ve noticed.
              </p>
              <CaregiverCheckIn />
            </motion.div>
          )}

          {view === "inbox" && (
            <motion.div key="inbox" {...fade}>
              <h2 className="text-xl font-semibold text-ink">Awaiting review</h2>
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
