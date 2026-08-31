/**
 * RecoveryLens — GuidancePanel
 *
 * Renders the cited guideline text behind each guidance trigger, plus the
 * clinician Q&A surface.
 *
 * The one rule this component exists to enforce
 * ---------------------------------------------
 * A reader must never be unable to tell OUR words from A GUIDELINE'S words.
 *
 * `plain_summary` is written by us. `excerpt` is verbatim from a published
 * guideline. They are therefore given deliberately different treatments:
 * our text is plain muted prose with an explicit "RecoveryLens note" label;
 * guideline text sits in a bordered block, in quotation marks, with the
 * publisher, section number and a link out. Do not harmonise these styles —
 * the visual difference IS the safety feature. If they ever look alike, we are
 * implicitly attributing our sentences to NICE.
 *
 * Colour discipline
 * -----------------
 * `amber` is reserved for genuine clinical meaning (see tailwind.config.js).
 * It is used here for evidence gaps and caveats, which qualify: "no guideline
 * covers this" is a clinical fact a reader must not miss. It is not used for
 * chrome, hovers or expand affordances.
 */

import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { api } from "../lib/api";
import type {
  GuidanceAnswer,
  GuidanceBlock,
  GuidanceBundle,
  GuidanceEntry,
  GuidanceSelection,
} from "../lib/api";

/** Verbatim guideline text. Never restyle this to match our own prose. */
function Excerpt({ entry }: { entry: GuidanceEntry }) {
  const isPrimary = entry.source.tier === "primary";

  return (
    <li className="border-l-2 border-accent-soft pl-3 py-1">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <a
          href={entry.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-xs text-accent hover:underline"
        >
          {entry.source.short_title} {entry.section}
        </a>
        {entry.heading && (
          <span className="text-xs text-muted">· {entry.heading}</span>
        )}
        {isPrimary && (
          <span className="rounded-sm bg-line px-1.5 py-0.5 text-2xs text-muted">
            {entry.source.jurisdiction} · primary source
          </span>
        )}
      </div>

      {/* Quotation marks are not decoration — they mark the boundary of text we
          did not write. */}
      <blockquote className="mt-1.5 text-sm text-ink">
        &ldquo;{entry.excerpt}&rdquo;
      </blockquote>

      {entry.caveat && (
        <p className="mt-1.5 text-xs text-warn/90">
          <span className="font-medium">Caveat:</span> {entry.caveat}
        </p>
      )}
    </li>
  );
}

function TriggerCard({ block }: { block: GuidanceBlock }) {
  const [open, setOpen] = useState(false);
  const reduce = useReducedMotion();
  const isGap = block.status === "evidence_gap";
  const panelId = `guidance-${block.trigger}`;

  return (
    <li className="card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span className="min-w-0">
          <span className="block text-sm font-medium text-ink">{block.label}</span>
          <span className="mt-0.5 block text-xs text-muted">
            {isGap ? (
              <span className="text-warn">No guideline recommendation found</span>
            ) : (
              `${block.entry_count} cited ${
                block.entry_count === 1 ? "recommendation" : "recommendations"
              }`
            )}
            {block.audience === "caregiver" && " · caregiver-facing"}
          </span>
        </span>

        <span
          aria-hidden
          className={`shrink-0 text-muted transition-transform ${open ? "rotate-90" : ""}`}
        >
          ›
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={panelId}
            initial={reduce ? undefined : { height: 0, opacity: 0 }}
            animate={reduce ? undefined : { height: "auto", opacity: 1 }}
            exit={reduce ? undefined : { height: 0, opacity: 0 }}
            transition={reduce ? undefined : { duration: 0.22, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="border-t border-line px-4 py-3 space-y-3">
              {/* OUR words. Labelled, muted, unquoted — deliberately unlike an
                  excerpt so attribution is never ambiguous. */}
              {block.plain_summary && (
                <p className="text-sm text-muted">
                  <span className="font-medium text-faint">RecoveryLens note — </span>
                  {block.plain_summary}
                </p>
              )}

              {isGap ? (
                <div className="rounded-md border border-warn/30 bg-warn/5 p-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-warn">
                    Evidence gap
                  </p>
                  <p className="mt-1.5 text-sm text-muted">{block.evidence_note}</p>
                </div>
              ) : (
                <>
                  <ul className="space-y-3">
                    {block.entries.map((e) => (
                      <Excerpt key={e.id} entry={e} />
                    ))}
                  </ul>
                  {block.evidence_note && (
                    <p className="text-xs text-warn/80">{block.evidence_note}</p>
                  )}
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

/** The retrieval surface. Kept visually separate from the deterministic cards
 *  above, because this one can decline and the cards above cannot. */
function AskBox() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<GuidanceAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.askGuidance(question.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4">
      <h3 className="text-sm font-medium text-ink">Ask the guidance corpus</h3>
      <p className="mt-1 text-xs text-muted">
        Clinician-facing. Searches the cited guidelines only — it will say so
        when it has nothing relevant rather than guessing.
      </p>

      <form onSubmit={submit} className="mt-3 flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. are wrist splints recommended?"
          aria-label="Question for the guidance corpus"
          className="field-input flex-1"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="btn-secondary"
        >
          {busy ? "Searching…" : "Ask"}
        </button>
      </form>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {result && (
        <div className="mt-4 space-y-3" aria-live="polite">
          {!result.answered ? (
            <div className="rounded-md border border-line p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted">
                No answer available
              </p>
              <p className="mt-1.5 text-sm text-muted">{result.answer}</p>
            </div>
          ) : (
            <>
              {/* Mode is shown, not hidden: a clinician should know whether they
                  are reading guideline text or a model's paraphrase of it. */}
              <p className="text-xs text-muted">
                {result.mode === "synthesised"
                  ? "Generated from the passages below, restricted to their content."
                  : "Passages returned verbatim — no text was generated."}
              </p>

              {result.mode === "synthesised" && (
                <p className="whitespace-pre-line text-sm text-ink">{result.answer}</p>
              )}

              <ul className="space-y-3">
                {result.passages.map((p) => (
                  <Excerpt key={p.id} entry={p} />
                ))}
              </ul>
            </>
          )}

          <p className="text-xs text-faint">{result.disclaimer}</p>
        </div>
      )}
    </div>
  );
}

/** How the topics were chosen. Shown because the selection is now a model's
 *  judgement rather than an if/else, and a clinician should be able to see the
 *  reasoning and disagree with it. */
function SelectionTrace({ sel }: { sel: GuidanceSelection }) {
  const [open, setOpen] = useState(false);
  if (!sel) return null;

  const agentPicked = sel.selections.filter((s) => s.source === "agent");
  const label =
    sel.mode === "agent" ? `Selected by agent · ${agentPicked.length} topics`
    : sel.mode === "agent_failed" ? "Agent unavailable — rules applied"
    : "Selected by rules";

  return (
    <div className="card p-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <span className="text-sm text-ink">{label}</span>
        <span aria-hidden className={`text-muted transition-transform ${open ? "rotate-90" : ""}`}>›</span>
      </button>

      {open && (
        <div className="mt-3 space-y-3 border-t border-line pt-3">
          {sel.agent_summary && (
            <p className="whitespace-pre-line text-sm text-muted">{sel.agent_summary}</p>
          )}

          <ul className="space-y-2">
            {sel.selections.map((s) => (
              <li key={s.topic} className="text-sm">
                <span className="text-ink">{s.topic.replace(/_/g, " ")}</span>
                {s.source === "rule" && (
                  <span className="ml-2 rounded-sm bg-line px-1.5 py-0.5 text-2xs text-muted">
                    rule
                  </span>
                )}
                <span className="block text-muted">{s.rationale}</span>
              </li>
            ))}
          </ul>

          {sel.tool_calls.length > 0 && (
            <div>
              <p className="section-label">What it checked</p>
              <ul className="mt-1">
                {sel.tool_calls.map((c, i) => (
                  <li key={i} className="font-mono text-xs text-muted">· {c.name}</li>
                ))}
              </ul>
            </div>
          )}

          {sel.mode === "agent_failed" && (
            <p className="text-xs text-warn/90">
              {sel.agent_error} — the deterministic rules still selected guidance,
              so nothing was missed.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function GuidancePanel({ bundle, selection }: {
  bundle: GuidanceBundle;
  selection?: GuidanceSelection;
}) {
  if (!bundle?.guidance?.length) return null;

  return (
    <section aria-label="Guidance" className="mt-8 space-y-4">
      <header>
        <h2 className="text-lg font-semibold text-ink">Guidance</h2>
        <p className="mt-1 max-w-prose text-sm text-muted">
          Topics chosen for this patient&rsquo;s risk profile and recorded deficits.
          The recommendations themselves are quoted verbatim from published
          guidelines — only the choice of which apply is made per patient.
        </p>
      </header>

      {selection && <SelectionTrace sel={selection} />}

      {/* Drift alarm. Should always be empty; if it is not, the model is emitting
          a trigger the corpus cannot resolve and someone must fix it. */}
      {bundle.unresolved_triggers.length > 0 && (
        <div className="rounded-md border border-danger/40 bg-danger/5 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-danger">
            Unresolved triggers
          </p>
          <p className="mt-1 text-sm text-muted">
            {bundle.unresolved_triggers.join(", ")} — emitted by the model but absent
            from the guidance corpus. Report this.
          </p>
        </div>
      )}

      <ul className="space-y-2">
        {bundle.guidance.map((b) => (
          <TriggerCard key={b.trigger} block={b} />
        ))}
      </ul>

      <AskBox />

      <details className="card p-4">
        <summary className="cursor-pointer text-sm font-medium text-ink">
          Sources ({bundle.sources_cited.length})
        </summary>
        <ul className="mt-3 space-y-2">
          {bundle.sources_cited.map((s) => (
            <li key={s.id} className="text-sm">
              <a
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline"
              >
                {s.short_title}
              </a>
              <span className="text-muted"> — {s.title}</span>
              <span className="block text-xs text-faint">
                {s.publisher}
                {s.published && ` · ${s.published}`}
                {s.jurisdiction && ` · ${s.jurisdiction}`}
                {s.tier === "primary" && " · primary source"}
              </span>
            </li>
          ))}
        </ul>
      </details>

      <p className="max-w-prose text-xs text-faint">{bundle.disclaimer}</p>
    </section>
  );
}
