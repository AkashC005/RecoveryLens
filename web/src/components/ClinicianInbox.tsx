/**
 * RecoveryLens — ClinicianInbox
 *
 * Escalations awaiting review, with the reasoning behind each one.
 *
 * The design rule here: a clinician must be able to see WHY something was
 * flagged and BY WHAT. Rule-raised reasons and agent-raised reasons are shown
 * separately and labelled, never merged into one sentence. Two reasons:
 *
 *   1. Trust is calibrated per-source. A clinician who learns the agent
 *      over-flags can weight it accordingly — but only if they can tell which
 *      flags came from it.
 *   2. An escalation nobody can audit is one they will eventually ignore, and an
 *      ignored inbox is worse than no inbox.
 *
 * The tool trace is shown too. It is the difference between "the AI flagged
 * this" and "it read the risk profile, checked the last three check-ins, looked
 * up the guidance, and then flagged this" — the second is reviewable.
 */

import { useEffect, useState } from "react";
import { api, URGENCY_STYLES } from "../lib/api";
import type { AiStatus, Escalation } from "../lib/api";

const URGENCY_ORDER = { urgent: 0, soon: 1, routine: 2 } as const;

function AgentTrace({ e }: { e: Escalation }) {
  const [open, setOpen] = useState(false);
  const t = e.triage;
  if (!t) return null;

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-xs text-teal"
      >
        {open ? "Hide reasoning" : "How this was flagged"}
      </button>

      {open && (
        <div className="mt-2 space-y-3 border-l-2 border-raised pl-3">
          {t.rule_reasons.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Raised by rule checks
              </p>
              <ul className="mt-1 space-y-0.5">
                {t.rule_reasons.map((r) => (
                  <li key={r} className="text-sm text-bone">{r}</li>
                ))}
              </ul>
            </div>
          )}

          {t.agent_reasons.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Raised by the triage agent
              </p>
              <ul className="mt-1 space-y-0.5">
                {t.agent_reasons.map((r) => (
                  <li key={r} className="text-sm text-bone">{r}</li>
                ))}
              </ul>
            </div>
          )}

          {t.agent_summary && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Agent notes
              </p>
              <p className="mt-1 whitespace-pre-line text-sm text-muted">
                {t.agent_summary}
              </p>
            </div>
          )}

          {t.tool_calls.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                What it checked
              </p>
              <ul className="mt-1 space-y-0.5">
                {t.tool_calls.map((c, i) => (
                  <li key={i} className="font-mono text-xs text-muted">
                    {c.ok ? "·" : "×"} {c.name}
                    {c.error && <span className="text-signal"> — {c.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {t.mode === "agent_failed" && (
            <p className="text-xs text-amber/90">
              The agent could not run ({t.agent_error}). The rule checks above
              still applied — nothing was missed because of this.
            </p>
          )}
          {t.mode === "rules_only" && (
            <p className="text-xs text-muted">
              Agent disabled. Free-text notes were not read.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function ClinicianInbox() {
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
                  <span className="text-sm text-bone">
                    {e.patient_ref || `Patient #${e.patient_id}`}
                  </span>
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

                <AgentTrace e={e} />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
