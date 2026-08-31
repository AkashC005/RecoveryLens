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

import { useEffect, useState } from "react";
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

/** Counts a number up to its target, in step with the bar beside it.
 *
 *  Shares the bar's ease-out-cubic so the digits stop at the same instant the
 *  bar stops moving. Two eased animations of the same value landing at different
 *  times reads as a bug even when nobody can say why.
 *
 *  Returns the target immediately when motion is reduced — the count-up is
 *  decoration, and the number is the information. */
function useCountUp(target: number, animate: boolean): number {
  const [value, setValue] = useState(animate ? 0 : target);

  useEffect(() => {
    if (!animate) {
      setValue(target);
      return;
    }
    let frame = 0;
    const started = performance.now();
    const DURATION = 700;
    const tick = (now: number) => {
      const t = Math.min(1, (now - started) / DURATION);
      setValue(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, animate]);

  return value;
}

/** One outcome, as a position on a scale rather than a coloured dot.
 *
 * WHY A PERCENTILE BAR AND NOT A GAUGE
 * ------------------------------------
 * The models rank; they do not produce calibrated probability. IST-3 validation
 * showed the ordering transports to a modern cohort and the absolute numbers do
 * not. A dial pointing at "73%" would claim a precision this system has never
 * demonstrated, so the bar is explicitly a POSITION AMONG PATIENTS — the axis is
 * the trial cohort, and it is labelled as such.
 *
 * The raw probability stays available and stays quiet, for the same reason.
 *
 * `actionability` dims the card rather than hiding it. An exploratory outcome is
 * still a real output of the model; presenting it identically to an actionable
 * one would invite someone to act on the weakest thing on the screen.
 */
function RiskCard({ risk, index = 0 }: { risk: RiskResult; index?: number }) {
  const style = TIER_STYLES[risk.tier];
  const reduce = useReducedMotion();
  const dimmed = risk.actionability !== "actionable";

  // Staggered so the eye lands on one bar at a time instead of six moving at
  // once. Capped, because a sixth card should not wait most of a second.
  const delay = reduce ? 0 : Math.min(index, 5) * 0.08;
  const shown = useCountUp(risk.percentile, !reduce);

  return (
    <div
      className={`card p-5 transition-shadow duration-200 hover:shadow-lift ${
        dimmed ? "opacity-75" : ""
      }`}
      aria-label={`${risk.label}: ${TIER_LABEL[risk.tier]} risk, ${risk.percentile}th percentile`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-base font-semibold text-ink">{risk.label}</p>
          <span className={`${style.chip} mt-1.5`}>
            <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden />
            {TIER_LABEL[risk.tier]}
          </span>
        </div>

        <div className="shrink-0 text-right">
          <p className={`tabular text-3xl font-semibold leading-none ${style.text}`}>
            {shown}
            <span className="align-super text-sm font-medium">th</span>
          </p>
          <p className="mt-1 text-2xs uppercase tracking-widest text-faint">
            percentile
          </p>
        </div>
      </div>

      {/* The scale. Quartile ticks give the bar a frame of reference — without
          them a filled bar is just a coloured rectangle and the reader has no
          way to judge whether it is long. */}
      <div className="mt-4">
        <div className={`relative h-2.5 overflow-hidden rounded-full ${style.track}`}>
          {[25, 50, 75].map((t) => (
            <span
              key={t}
              aria-hidden
              className="absolute top-0 z-10 h-full w-px bg-canvas/60"
              style={{ left: `${t}%` }}
            />
          ))}
          <motion.div
            className={`h-full rounded-full ${style.bar}`}
            initial={reduce ? false : { width: 0 }}
            animate={{ width: `${Math.max(2, Math.min(100, risk.percentile))}%` }}
            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay }}
          />
        </div>
        <div className="mt-1.5 flex justify-between text-2xs text-faint">
          <span>lower risk</span>
          {/* Named, not implied. The axis is a 1990s trial population, and a
              reader who assumes it is "patients like mine today" has been
              misled by the chart rather than by anything we wrote. */}
          <span>ranked against the IST-1 cohort</span>
          <span>higher risk</span>
        </div>
      </div>

      {dimmed && (
        <p className="mt-3 section-label">
          {risk.actionability === "vigilance" ? "Vigilance only" : "Exploratory"}
        </p>
      )}

      {risk.drivers.length > 0 && (
        <div className="mt-4 border-t border-line-soft pt-3">
          <p className="section-label">What moved this</p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {risk.drivers.slice(0, 4).map((d) => (
              <li
                key={d.factor}
                className="inline-flex items-center gap-1.5 rounded-md bg-sunken px-2 py-1 text-xs text-ink-soft"
              >
                <span
                  aria-hidden
                  className={d.direction === "increases" ? "text-warn" : "text-calm"}
                >
                  {d.direction === "increases" ? "\u25b2" : "\u25bc"}
                </span>
                {d.factor}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 text-xs text-muted">{risk.note}</p>
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
        <span className={meta.className}>
          {meta.label}
        </span>
        {c.narrative_mode === "synthesised" && (
          <span className="text-2xs text-muted">generated from cited guidance</span>
        )}
      </div>

      {/* Caregiver text leads: this is the message that actually gets sent. */}
      <p className="mt-2 text-sm text-ink">{c.caregiver_message}</p>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mt-2 text-xs text-accent"
      >
        {open ? "Hide" : "Clinician view and evidence"}
      </button>

      {open && (
        <div className="mt-2 space-y-3 border-l-2 border-line pl-3">
          <div>
            <p className="section-label">Clinician</p>
            <p className="mt-1 text-sm text-muted">{c.clinician_note}</p>
          </div>

          <div>
            <p className="section-label">
              Why this day
            </p>
            <p className="mt-1 text-sm text-muted">{c.basis_explained}</p>
            {c.evidence_note && (
              <p className="mt-1 text-xs text-warn/90">{c.evidence_note}</p>
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
                    className="font-mono text-xs text-accent hover:underline"
                  >
                    {e.source.short_title} {e.section}
                  </a>
                  <blockquote className="mt-1 text-sm text-ink">
                    &ldquo;{e.excerpt}&rdquo;
                  </blockquote>
                  {e.caveat && (
                    <p className="mt-1 text-xs text-warn/90">{e.caveat}</p>
                  )}
                </li>
              ))}
            </ul>
          )}

          {c.passages.length > 0 && (
            <div>
              <p className="section-label">
                Retrieved for this patient
              </p>
              <ul className="mt-1 space-y-1">
                {c.passages.map((p) => (
                  <li key={p.id} className="text-xs">
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-mono text-accent hover:underline"
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
        <h2 className="text-lg font-semibold text-ink">Recovery timeline</h2>
        <p className="text-sm text-muted mt-1 max-w-prose">
          Risks are placed at the point in recovery where they matter. Tiers show
          standing relative to comparable patients — not calibrated probability.
        </p>
      </header>

      <div className="relative pl-8">
        <motion.div
          {...spine}
          style={{ originY: 0 }}
          className="absolute left-[7px] top-1 bottom-1 w-px bg-accent-soft"
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
                className={`absolute -left-8 top-1.5 h-3.5 w-3.5 rounded-full border-2 border-canvas shadow-card ${
                  event.kind === "discharge" ? "bg-ink" : "bg-accent"
                }`}
              />

              <div className="flex items-baseline gap-3">
                <span className="font-mono text-xs text-muted">
                  {event.day === 0 ? "DAY 0" : `DAY ${event.day}`}
                </span>
                <h3 className="text-base font-medium text-ink">{event.title}</h3>
              </div>

              {event.detail && (
                <p className="text-sm text-muted mt-0.5">{event.detail}</p>
              )}

              {event.checkin && <CheckInCard c={event.checkin} />}

              {event.risks && (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {event.risks.map((risk, n) => (
                    <RiskCard key={risk.outcome} risk={risk} index={n} />
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

      <p className="mt-6 text-xs text-faint max-w-prose">{result.disclaimer}</p>
    </section>
  );
}
