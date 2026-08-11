/**
 * RecoveryLens — PatientDetail
 *
 * One patient's full record. Everything here was already in the database and
 * already served by GET /api/patients/{id}; nothing in the frontend called it,
 * so the data existed and was unreachable.
 *
 * What this screen is FOR
 * ----------------------
 * Answering "what do I need to know about this person right now?" in one pass.
 * That question has four parts, and they are laid out in that order:
 *
 *   1. Is follow-up actually working?  ← the messaging banner, first, deliberately
 *   2. What is the risk picture?       ← tiers from the latest assessment
 *   3. What has happened so far?       ← the check-in trail with triage traces
 *   4. What was entered?               ← assessment inputs, collapsed
 *
 * Why messaging comes first
 * ------------------------
 * Every other panel describes a plan. The messaging state says whether the plan
 * is being executed. A patient with a perfect risk profile and a carer who
 * opted out is receiving nothing, and that is the single most important fact on
 * the page — but it is invisible everywhere else in the app. It sits at the top
 * because a silent failure that requires scrolling is still a silent failure.
 *
 * `can_send` and `blocked_reason` are read straight from the API, which gets
 * them from the same `may_send()` gate the send endpoint calls. This screen must
 * never re-derive them from `consent_recorded` and friends — that is how a UI
 * ends up promising a send the gate refuses.
 */

import { useEffect, useState } from "react";
import {
  api,
  CHECKIN_STATUS_META,
  TIER_LABEL,
  TIER_STYLES,
  URGENCY_STYLES,
} from "../lib/api";
import type {
  AssessmentRecord,
  CheckInRecord,
  MessagingState,
  PatientDetail as Detail,
  RiskResult,
  Tier,
} from "../lib/api";
import AgentTrace from "./AgentTrace";

// --------------------------------------------------------------------- pieces

function MessagingBanner({ m }: { m: MessagingState }) {
  // Three states worth distinguishing, because the actions differ completely.
  const tone = m.opted_out
    ? "border-signal/50"
    : m.can_send
      ? "border-teal-dim"
      : "border-amber/40";

  return (
    <section className={`card border ${tone} p-4`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-medium text-bone">Follow-up delivery</h3>
        <span
          className={`text-xs ${
            m.can_send ? "text-teal" : m.opted_out ? "text-signal" : "text-amber"
          }`}
        >
          {m.can_send ? "Messages can be sent" : "Messages are not being sent"}
        </span>
      </div>

      {!m.can_send && m.blocked_reason && (
        <p className="mt-2 text-sm text-bone">{m.blocked_reason}</p>
      )}

      <dl className="mt-3 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted">Carer contact</dt>
          <dd className="font-mono text-bone">
            {m.caregiver_contact_on_file
              ? (m.contact_hint ?? "on file")
              : "none on file"}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Consent recorded</dt>
          <dd className={m.consent_recorded ? "text-bone" : "text-amber"}>
            {m.consent_recorded ? "Yes" : "No — nothing will be sent"}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Last heard from carer</dt>
          <dd className="text-bone">
            {m.last_inbound_at
              ? new Date(m.last_inbound_at).toLocaleString()
              : "never"}
          </dd>
        </div>
        <div>
          <dt className="text-muted">WhatsApp 24-hour window</dt>
          <dd className={m.whatsapp_window_open ? "text-teal" : "text-muted"}>
            {m.whatsapp_window_open ? "Open" : "Closed"}
          </dd>
        </div>
      </dl>

      {/* Stated rather than hidden: this constraint is Meta's, applies however
          well the app is built, and a reviewer who discovers it themselves
          trusts the rest of the screen less. */}
      <p className="mt-3 border-t border-raised pt-2 text-xs text-muted">
        {m.whatsapp_window_note}
      </p>

      {m.opted_out && (
        <p className="mt-2 text-xs text-signal">
          Opted out
          {m.opted_out_at && ` on ${new Date(m.opted_out_at).toLocaleString()}`}.
          This is permanent and outranks consent — only a person can clear it.
        </p>
      )}
    </section>
  );
}

function RiskGrid({ risks }: { risks: RiskResult[] }) {
  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {risks.map((r) => {
        const s = TIER_STYLES[r.tier] ?? TIER_STYLES.low;
        return (
          <li key={r.outcome} className="rounded-md border border-raised p-3">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 shrink-0 rounded-full ${s.dot}`} aria-hidden />
              <p className="min-w-0 flex-1 truncate text-sm text-bone">{r.label}</p>
              <span className={`text-xs ${s.text}`}>
                {TIER_LABEL[r.tier] ?? r.tier}
              </span>
            </div>
            {/* Percentile, not probability. The models are from 1991-96 trial
                data; the rank is transportable and the absolute number is not,
                which IST-3 validation showed empirically. */}
            <p className="mt-1 font-mono text-[11px] text-muted">
              {r.percentile}th percentile · {r.actionability}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

function CheckInRow({ c }: { c: CheckInRecord }) {
  const meta = CHECKIN_STATUS_META[c.status] ?? CHECKIN_STATUS_META.scheduled;
  const urgency = URGENCY_STYLES[c.urgency] ?? URGENCY_STYLES.routine;
  const note = (c.responses?.free_text as string) || "";

  return (
    <li className="border-l-2 border-raised pl-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 text-[11px] ${meta.className}`}>
          {meta.label}
        </span>
        {c.escalated && (
          <span className={`rounded-full border px-2 py-0.5 text-[11px] ${urgency.className}`}>
            {urgency.label}
          </span>
        )}
        <span className="text-sm text-bone">{c.reason || "Check-in"}</span>
        <span className="ml-auto font-mono text-[11px] text-muted">
          {new Date(c.scheduled_for).toLocaleDateString()}
        </span>
      </div>

      <p className="mt-1 text-xs text-muted">{meta.hint}</p>

      {c.escalation_reason && (
        <p className="mt-2 text-sm text-bone">{c.escalation_reason}</p>
      )}

      {note && (
        <blockquote className="mt-2 border-l-2 border-teal-dim pl-3 text-sm text-muted">
          &ldquo;{note}&rdquo;
        </blockquote>
      )}

      <AgentTrace triage={c.triage} labelClosed="How this was triaged" />
    </li>
  );
}

/** The assessment as submitted. Collapsed by default — needed to argue with a
 *  tier, not needed to read one. */
function InputsPanel({ a }: { a: AssessmentRecord }) {
  const [open, setOpen] = useState(false);
  if (!a.inputs) return null;

  const entries = Object.entries(a.inputs).filter(
    ([, v]) => v !== null && v !== undefined && v !== false && v !== "absent",
  );

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-xs text-teal"
      >
        {open ? "Hide what was entered" : "What was entered at discharge"}
      </button>
      {open && (
        <dl className="mt-2 grid gap-x-6 gap-y-1 border-l-2 border-raised pl-3 text-xs sm:grid-cols-2">
          {entries.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
              <dd className="font-mono text-bone">{String(v)}</dd>
            </div>
          ))}
          <p className="mt-2 text-muted sm:col-span-2">
            Defaults and absent findings are hidden. {entries.length} of{" "}
            {Object.keys(a.inputs).length} fields shown.
          </p>
        </dl>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------- main

export default function PatientDetail({
  patientId,
  onBack,
}: {
  patientId: number;
  onBack: () => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setError(null);
    api.patient(patientId).then(setD).catch((e) => setError(String(e)));
  }, [patientId]);

  const back = (
    <button onClick={onBack} className="mb-6 text-sm text-teal hover:text-bone">
      ← All patients
    </button>
  );

  if (error) return <div>{back}<p className="text-sm text-signal">{error}</p></div>;
  if (!d) return <div>{back}<p className="text-sm text-muted">Loading…</p></div>;

  const latest = d.assessments[0];
  const risks = latest?.results?.risks ?? [];

  return (
    <div>
      {back}

      <header className="mb-6">
        <h2 className="text-xl font-semibold text-bone">
          {d.patient_ref || <span className="text-muted">Unlabelled patient</span>}
        </h2>
        <p className="mt-1 font-mono text-xs text-muted">
          #{d.id} · first assessed {new Date(d.created_at).toLocaleDateString()} ·{" "}
          {d.assessments.length} assessment{d.assessments.length === 1 ? "" : "s"}
          {d.open_escalations > 0 && (
            <span className="text-signal">
              {" "}· {d.open_escalations} escalation
              {d.open_escalations === 1 ? "" : "s"}
            </span>
          )}
        </p>
      </header>

      <div className="space-y-6">
        <MessagingBanner m={d.messaging} />

        {risks.length > 0 && (
          <section>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
              <h3 className="text-sm font-medium text-bone">Risk profile</h3>
              <span className="text-xs text-muted">
                from the assessment of{" "}
                {new Date(latest.created_at).toLocaleDateString()}
              </span>
            </div>
            <RiskGrid risks={risks} />
            {latest.guidance_triggers.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {latest.guidance_triggers.map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-raised px-2 py-0.5 text-[11px] text-muted"
                  >
                    {t.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            )}
            <InputsPanel a={latest} />
          </section>
        )}

        <section>
          <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
            <h3 className="text-sm font-medium text-bone">Follow-up trail</h3>
            {d.next_check_in && (
              <span className="text-xs text-muted">
                next due {new Date(d.next_check_in).toLocaleDateString()}
              </span>
            )}
          </div>
          {d.check_ins.length === 0 ? (
            <p className="text-sm text-muted">No check-ins scheduled.</p>
          ) : (
            <ul className="space-y-4">
              {d.check_ins.map((c) => (
                <CheckInRow key={c.id} c={c} />
              ))}
            </ul>
          )}
        </section>

        {d.assessments.length > 1 && (
          <section>
            <h3 className="mb-3 text-sm font-medium text-bone">
              Earlier assessments
            </h3>
            <ul className="space-y-3">
              {d.assessments.slice(1).map((a) => (
                <li key={a.id} className="rounded-md border border-raised p-3">
                  <p className="text-sm text-bone">
                    {new Date(a.created_at).toLocaleString()}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {(a.results?.risks ?? []).map((r) => (
                      <span
                        key={r.outcome}
                        title={`${r.label}: ${r.tier}`}
                        className={`h-2 w-2 rounded-full ${
                          TIER_STYLES[r.tier as Tier]?.dot ?? "bg-muted"
                        }`}
                      />
                    ))}
                  </div>
                  <InputsPanel a={a} />
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
