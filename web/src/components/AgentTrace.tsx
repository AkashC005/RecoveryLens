/**
 * RecoveryLens — AgentTrace
 *
 * How one escalation was decided. Extracted from ClinicianInbox so the patient
 * record can show the same thing: two screens rendering triage two different
 * ways is how a reviewer ends up trusting one and not the other.
 *
 * The design rule it carries with it: rule-raised and agent-raised reasons are
 * shown separately and labelled, never merged into one sentence.
 *
 *   1. Trust is calibrated per source. A clinician who learns the agent
 *      over-flags can weight it accordingly — but only if they can tell which
 *      flags came from it.
 *   2. An escalation nobody can audit is one they will eventually ignore, and an
 *      ignored inbox is worse than no inbox.
 *
 * The tool trace is the difference between "the AI flagged this" and "it read
 * the risk profile, checked the last three check-ins, looked up the guidance,
 * and then flagged this". Only the second is reviewable.
 */

import { useState } from "react";
import type { TriageRecord } from "../lib/api";

export default function AgentTrace({
  triage,
  labelClosed = "How this was flagged",
}: {
  triage: TriageRecord | null;
  labelClosed?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!triage) return null;
  const t = triage;

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-xs text-accent"
      >
        {open ? "Hide reasoning" : labelClosed}
      </button>

      {open && (
        <div className="mt-2 space-y-3 border-l-2 border-line pl-3">
          {t.rule_reasons.length > 0 && (
            <div>
              <p className="section-label">
                Raised by rule checks
              </p>
              <ul className="mt-1 space-y-0.5">
                {t.rule_reasons.map((r) => (
                  <li key={r} className="text-sm text-ink">{r}</li>
                ))}
              </ul>
            </div>
          )}

          {t.agent_reasons.length > 0 && (
            <div>
              <p className="section-label">
                Raised by the triage agent
              </p>
              <ul className="mt-1 space-y-0.5">
                {t.agent_reasons.map((r) => (
                  <li key={r} className="text-sm text-ink">{r}</li>
                ))}
              </ul>
            </div>
          )}

          {t.agent_summary && (
            <div>
              <p className="section-label">
                Agent notes
              </p>
              <p className="mt-1 whitespace-pre-line text-sm text-muted">
                {t.agent_summary}
              </p>
            </div>
          )}

          {t.tool_calls.length > 0 && (
            <div>
              <p className="section-label">
                What it checked
              </p>
              <ul className="mt-1 space-y-0.5">
                {t.tool_calls.map((c, i) => (
                  <li key={i} className="font-mono text-xs text-muted">
                    {c.ok ? "·" : "×"} {c.name}
                    {c.error && <span className="text-danger"> — {c.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {t.mode === "agent_failed" && (
            <p className="text-xs text-warn/90">
              The agent could not run ({t.agent_error}). The rule checks above
              still applied — nothing was missed because of this.
            </p>
          )}
          {t.mode === "rules_only" && (
            <p className="text-xs text-muted">
              {t.skipped_because === "no_free_text"
                ? "The carer answered the questions but wrote no note, so there " +
                  "was nothing for the agent to read. The flags above come from " +
                  "the rule checks."
                : t.skipped_because === "disabled"
                  ? "Triage agent is switched off (RECOVERYLENS_TRIAGE_AGENT). " +
                    "Free-text notes were not read."
                  : "The agent has not reported on this check-in. It runs in the " +
                    "background after a reply; reload in a moment."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
