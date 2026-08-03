/**
 * RecoveryLens — RiskTimeline
 *
 * The signature element. The five outcomes are *time-indexed* (14 days,
 * 6 months), so they are laid on a vertical spine running forward from
 * discharge rather than tiled as a card grid. Scheduled check-ins sit on the
 * same spine. The structure encodes something true: this tool is about time
 * after discharge.
 *
 * No gauges, no speedometers, no giant percentages. The model does relative
 * ranking, not calibrated probability — a dial pointing at "73%" would claim a
 * precision it does not have. Tier and percentile lead; the raw probability is
 * available but deliberately quiet.
 */

import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import type { AssessmentResponse, EnrichedCheckIn, RiskResult } from "../lib/api";
import { BASIS_META, TIER_LABEL, TIER_STYLES } from "../lib/api";
import GuidancePanel from "./GuidancePanel";

interface TimelineEvent {
  day: number;
  kind: "discharge" | "checkin" | "risks";
  title: string;
  detail?: string;
  risks?: RiskResult[];
  checkin?: EnrichedCheckIn;
}

function buildEvents(result: AssessmentResponse): TimelineEvent[] {
  const events: TimelineEvent[] = [
    { day: 0, kind: "discharge", title: "Discharge", detail: "Assessment recorded" },
  ];

  // Prefer the enriched timeline; fall back to the bare schedule if an older
  // API build is serving, so the page never renders empty.
  const checkins: EnrichedCheckIn[] =
    result.timeline?.length
      ? result.timeline
      : result.followup_plan.map((s) => ({
          day: s.day, label: s.reason, reason: s.reason,
          basis: "operational" as const,
          basis_explained: "", citations: [], passages: [],
          evidence_note: null, clinician_note: s.reason,
          caregiver_message: s.reason, narrative_mode: "static" as const,
        }));

  for (const c of checkins) {
    events.push({
      day: c.day,
      kind: "checkin",
      title: `Day ${c.day} — ${c.label}`,
      checkin: c,
    });
  }

  // Group risks by horizon so each appears at the point it becomes relevant.
  const byHorizon = new Map<number, RiskResult[]>();
  for (const risk of result.risks) {
    const bucket = byHorizon.get(risk.horizon_days) ?? [];
    bucket.push(risk);
    byHorizon.set(risk.horizon_days, bucket);
  }
  for (const [days, risks] of byHorizon) {
    events.push({
      day: days,
      kind: "risks",
      title: days <= 14 ? `${days}-day outlook` : `${Math.round(days / 30)}-month outlook`,
      risks,
    });
  }

  return events.sort((a, b) => a.day - b.day || (a.kind === "risks" ? 1 : -1));
}

function RiskCard({ risk }: { risk: RiskResult }) {
  const style = TIER_STYLES[risk.tier];
  const dimmed = risk.actionability !== "actionable";

  return (
    <div
      className={`card p-4 ${dimmed ? "opacity-70" : ""}`}
      aria-label={`${risk.label}: ${TIER_LABEL[risk.tier]} risk`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-base font-medium text-bone">{risk.label}</p>
          <p className={`text-sm font-medium ${style.text} mt-0.5`}>
            {TIER_LABEL[risk.tier]}
            <span className="text-muted font-normal">
              {" "}· {risk.percentile}th percentile
            </span>
          </p>
        </div>
        <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${style.dot}`} />
      </div>

      {risk.actionability !== "actionable" && (
        <p className="mt-2 text-xs uppercase tracking-wide text-muted">
          {risk.actionability === "vigilance" ? "Vigilance only" : "Exploratory"}
        </p>
      )}

      {risk.drivers.length > 0 && (
        <ul className="mt-3 space-y-1">
          {risk.drivers.slice(0, 3).map((d) => (
            <li key={d.factor} className="text-sm text-muted flex gap-2">
              <span aria-hidden className={d.direction === "increases" ? "text-amber" : "text-teal"}>
                {d.direction === "increases" ? "▲" : "▼"}
              </span>
              <span>{d.factor}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-muted/80">{risk.note}</p>
    </div>
  );
}

/** One scheduled check-in: its evidence basis, both narratives, and citations.
 *
 *  The basis chip is not decoration. A reader must be able to see instantly
 *  whether a check-in date carries published backing or is our own scheduling
 *  choice — the previous version of this product displayed "Guideline-recommended
 *  post-discharge review" on a day no guideline recommends. */
function CheckInCard({ c }: { c: EnrichedCheckIn }) {
  const [open, setOpen] = useState(false);
  const meta = BASIS_META[c.basis];

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 text-[11px] ${meta.className}`}>
          {meta.label}
        </span>
        {c.narrative_mode === "synthesised" && (
          <span className="text-[11px] text-muted">generated from cited guidance</span>
        )}
      </div>

      {/* Caregiver text leads: this is the message that actually gets sent. */}
      <p className="mt-2 text-sm text-bone">{c.caregiver_message}</p>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mt-2 text-xs text-teal"
      >
        {open ? "Hide" : "Clinician view and evidence"}
      </button>

      {open && (
        <div className="mt-2 space-y-3 border-l-2 border-raised pl-3">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Clinician</p>
            <p className="mt-1 text-sm text-muted">{c.clinician_note}</p>
          </div>

          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">
              Why this day
            </p>
            <p className="mt-1 text-sm text-muted">{c.basis_explained}</p>
            {c.evidence_note && (
              <p className="mt-1 text-xs text-amber/90">{c.evidence_note}</p>
            )}
          </div>

          {c.citations.length > 0 && (
            <ul className="space-y-2">
              {c.citations.map((e) => (
                <li key={e.id}>
                  <a
                    href={e.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-xs text-teal hover:underline"
                  >
                    {e.source.short_title} {e.section}
                  </a>
                  <blockquote className="mt-1 text-sm text-bone">
                    &ldquo;{e.excerpt}&rdquo;
                  </blockquote>
                  {e.caveat && (
                    <p className="mt-1 text-xs text-amber/90">{e.caveat}</p>
                  )}
                </li>
              ))}
            </ul>
          )}

          {c.passages.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Retrieved for this patient
              </p>
              <ul className="mt-1 space-y-1">
                {c.passages.map((p) => (
                  <li key={p.id} className="text-xs">
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-mono text-teal hover:underline"
                    >
                      {p.source.short_title} {p.section}
                    </a>
                    <span className="text-muted"> — {p.excerpt}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function RiskTimeline({ result }: { result: AssessmentResponse }) {
  const reduce = useReducedMotion();
  const events = buildEvents(result);

  // The one orchestrated moment: the spine draws, then events resolve onto it
  // in time order. ~600ms total. Everything else in the app stays still.
  const spine = reduce
    ? { initial: {}, animate: {} }
    : {
        initial: { scaleY: 0 },
        animate: { scaleY: 1, transition: { duration: 0.45, ease: "easeOut" as const } },
      };

  return (
    <section aria-label="Recovery timeline" className="relative">
      <header className="mb-6">
        <h2 className="text-lg font-semibold text-bone">Recovery timeline</h2>
        <p className="text-sm text-muted mt-1 max-w-prose">
          Risks are placed at the point in recovery where they matter. Tiers show
          standing relative to comparable patients — not calibrated probability.
        </p>
      </header>

      <div className="relative pl-8">
        <motion.div
          {...spine}
          style={{ originY: 0 }}
          className="absolute left-[7px] top-1 bottom-1 w-px bg-teal-dim"
          aria-hidden
        />

        <ol className="space-y-8">
          {events.map((event, i) => (
            <motion.li
              key={`${event.day}-${event.kind}`}
              initial={reduce ? undefined : { opacity: 0, y: 8 }}
              animate={reduce ? undefined : { opacity: 1, y: 0 }}
              transition={reduce ? undefined : { delay: 0.35 + i * 0.07, duration: 0.28 }}
              className="relative"
            >
              <span
                aria-hidden
                className={`absolute -left-8 top-1.5 h-3.5 w-3.5 rounded-full border-2 border-ink ${
                  event.kind === "discharge" ? "bg-bone" : "bg-teal"
                }`}
              />

              <div className="flex items-baseline gap-3">
                <span className="font-mono text-xs text-muted">
                  {event.day === 0 ? "DAY 0" : `DAY ${event.day}`}
                </span>
                <h3 className="text-base font-medium text-bone">{event.title}</h3>
              </div>

              {event.detail && (
                <p className="text-sm text-muted mt-0.5">{event.detail}</p>
              )}

              {event.checkin && <CheckInCard c={event.checkin} />}

              {event.risks && (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {event.risks.map((risk) => (
                    <RiskCard key={risk.outcome} risk={risk} />
                  ))}
                </div>
              )}
            </motion.li>
          ))}
        </ol>
      </div>

      {/* Previously this rendered `guidance_triggers` as bare chips — category
          labels with nothing behind them. The API now returns the cited guideline
          text itself, so the chips are replaced by the real content. Falls back
          to nothing rather than to chips: a label with no content is a promise
          the product cannot keep. */}
      {result.guidance && (
        <GuidancePanel
          bundle={result.guidance}
          selection={result.guidance_selection}
        />
      )}

      <p className="mt-6 text-xs text-muted/80 max-w-prose">{result.disclaimer}</p>
    </section>
  );
}
