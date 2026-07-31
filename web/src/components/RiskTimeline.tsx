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

import { motion, useReducedMotion } from "framer-motion";
import type { AssessmentResponse, RiskResult } from "../lib/api";
import { TIER_LABEL, TIER_STYLES } from "../lib/api";
import GuidancePanel from "./GuidancePanel";

interface TimelineEvent {
  day: number;
  kind: "discharge" | "checkin" | "risks";
  title: string;
  detail?: string;
  risks?: RiskResult[];
}

function buildEvents(result: AssessmentResponse): TimelineEvent[] {
  const events: TimelineEvent[] = [
    { day: 0, kind: "discharge", title: "Discharge", detail: "Assessment recorded" },
  ];

  for (const step of result.followup_plan) {
    events.push({
      day: step.day,
      kind: "checkin",
      title: `Day ${step.day} — caregiver check-in`,
      detail: step.reason,
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
      {result.guidance && <GuidancePanel bundle={result.guidance} />}

      <p className="mt-6 text-xs text-muted/80 max-w-prose">{result.disclaimer}</p>
    </section>
  );
}
