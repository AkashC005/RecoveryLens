/**
 * RecoveryLens — ClinicianInbox
 *
 * Escalations awaiting review, most urgent first.
 *
 * The reasoning display lives in AgentTrace, shared with the patient record —
 * see that file for why rule-raised and agent-raised reasons are kept apart.
 *
 * Each row links through to the full patient record, because an escalation read
 * without the risk profile and the previous check-ins behind it is a sentence
 * with no context.
 */

import { useEffect, useState } from "react";
import { api, URGENCY_STYLES } from "../lib/api";
import type { AiStatus, Escalation } from "../lib/api";
import AgentTrace from "./AgentTrace";

const URGENCY_ORDER = { urgent: 0, soon: 1, routine: 2 } as const;

export default function ClinicianInbox({
  onOpenPatient,
}: {
  onOpenPatient?: (id: number) => void;
}) {
  const [rows, setRows] = useState<Escalation[] | null>(null);
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.escalations().then(setRows).catch((e) => setError(String(e)));
    api.triageStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  if (error) return <p className="text-sm text-signal">{error}</p>;
  if (!rows) return <p className="text-sm text-muted">Loading…</p>;

  const sorted = [...rows].sort(
    (a, b) => (URGENCY_ORDER[a.urgency] ?? 2) - (URGENCY_ORDER[b.urgency] ?? 2),
  );

  return (
    <div className="space-y-4">
      {status && !status.features.triage_agent.enabled && (
        <div className="card p-3">
          <p className="text-xs text-muted">
            Triage agent is off ({status.features.triage_agent.env}). Check-ins
            still escalate on the rule checks, but free-text notes from carers are
            not being read.
          </p>
        </div>
      )}
      {status && status.features.triage_agent.enabled && !status.api_key_configured && (
        <div className="card p-3">
          <p className="text-xs text-amber/90">
            Triage agent is enabled but no API key is configured, so it cannot
            run. Rule checks still apply.
          </p>
        </div>
      )}

      {sorted.length === 0 ? (
        <div className="card p-8 text-center">
          <p className="text-base text-bone">Nothing awaiting review</p>
          <p className="mt-1 text-sm text-muted">
            Escalated check-ins appear here.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {sorted.map((e) => {
            const u = URGENCY_STYLES[e.urgency] ?? URGENCY_STYLES.routine;
            const note = (e.responses?.free_text as string) || "";
            return (
              <li key={e.check_in_id} className="card p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${u.className}`}>
                    {u.label}
                  </span>
                  {onOpenPatient ? (
                    <button
                      type="button"
                      onClick={() => onOpenPatient(e.patient_id)}
                      className="text-sm text-bone underline decoration-raised underline-offset-4 hover:decoration-teal"
                    >
                      {e.patient_ref || `Patient #${e.patient_id}`}
                    </button>
                  ) : (
                    <span className="text-sm text-bone">
                      {e.patient_ref || `Patient #${e.patient_id}`}
                    </span>
                  )}
                  <span className="ml-auto font-mono text-xs text-muted">
                    {new Date(e.completed_at).toLocaleString()}
                  </span>
                </div>

                <p className="mt-2 text-sm text-bone">{e.reason}</p>

                {note && (
                  <blockquote className="mt-2 border-l-2 border-teal-dim pl-3 text-sm text-muted">
                    &ldquo;{note}&rdquo;
                  </blockquote>
                )}

                <AgentTrace triage={e.triage} />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
