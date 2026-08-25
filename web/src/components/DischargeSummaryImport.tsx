/**
 * RecoveryLens — DischargeSummaryImport
 *
 * Paste a discharge summary, get the form filled in, then check it.
 *
 * Why this never submits
 * ----------------------
 * Autofill is the first feature where a model writes into a CLINICAL INPUT
 * rather than a display surface. Everything downstream — six risk models, the
 * tier, the guidance topics, the follow-up schedule — is computed from these
 * fields. A wrong value here is not a wrong sentence on a screen; it silently
 * changes a risk calculation, and nobody knows to question it.
 *
 * So this fills the form and stops. Every machine-filled field is marked until
 * the clinician has looked at it, and each one shows the exact sentence from the
 * summary it came from. That is the voice read-back pattern applied to a
 * document: a human verifying a machine's reading before it counts.
 *
 * What it reports, and why all three matter
 * -----------------------------------------
 *   filled     — value plus the quote behind it
 *   not found  — the field was not in the document. Left BLANK, never guessed:
 *                a blank costs five seconds, a guess costs a wrong tier
 *   rejected   — the model proposed something and we refused it, with the
 *                reason. Usually the supporting quote was not actually in the
 *                document, which is what a hallucination looks like from here
 *
 * "It tried and I rejected it" is different information from "it found nothing",
 * and a clinician deciding whether to trust this needs both.
 */

import { useRef, useState } from "react";
import { api } from "../lib/api";
import type { Extraction } from "../lib/api";

export default function DischargeSummaryImport({
  onExtracted,
}: {
  /** Called with field -> value. The parent pre-fills; it does not submit. */
  onExtracted: (values: Record<string, unknown>, result: Extraction) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Extraction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function run(body: Blob | string, contentType: string) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.extract(body, contentType);
      setResult(r);
      if (r.fields.length) {
        onExtracted(
          Object.fromEntries(r.fields.map((f) => [f.name, f.value])), r);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read that");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <p className="text-base text-bone">Start from a discharge summary</p>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-xs text-teal"
        >
          {open ? "Hide" : "Paste or upload one"}
        </button>
      </div>
      <p className="mt-0.5 text-sm text-muted">
        Fills in what it can find, shows you the sentence behind each value, and
        leaves the rest blank for you.
      </p>

      {open && (
        <div className="mt-4 space-y-3">
          <textarea
            rows={8}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the discharge summary here…"
            className="field-input resize-y font-mono text-xs"
          />

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || !text.trim()}
              onClick={() => void run(text, "text/plain")}
              className="rounded-md border border-teal-dim px-4 py-2 text-sm text-teal
                         disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "Reading…" : "Read this summary"}
            </button>

            <button
              type="button"
              disabled={busy}
              onClick={() => fileInput.current?.click()}
              className="rounded-md border border-raised px-4 py-2 text-sm text-bone
                         hover:border-teal-dim disabled:opacity-40"
            >
              Upload a PDF
            </button>
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void run(file, "application/pdf");
                e.target.value = "";     // allow re-picking the same file
              }}
            />
          </div>

          <p className="text-xs text-muted/80">
            A photographed or scanned PDF has no text in it to read — paste the
            text instead. Nothing is saved until you submit the form yourself.
          </p>

          {error && <p className="text-sm text-signal">{error}</p>}

          {result && <Report result={result} />}
        </div>
      )}
    </div>
  );
}

function Report({ result }: { result: Extraction }) {
  const [showSources, setShowSources] = useState(false);

  if (result.error) {
    return (
      <div className="rounded-md border border-amber/40 p-3">
        <p className="text-sm text-amber">{result.error}</p>
        <p className="mt-1 text-xs text-muted">
          The form is untouched. Fill it in by hand.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-raised p-3" aria-live="polite">
      <p className="text-sm text-bone">
        Filled <strong className="text-teal">{result.found_count}</strong> of{" "}
        {result.extractable_count} fields.{" "}
        <span className="text-muted">
          Please check each one against the summary before submitting.
        </span>
      </p>

      <button
        type="button"
        onClick={() => setShowSources((v) => !v)}
        className="mt-2 text-xs text-teal"
      >
        {showSources ? "Hide what it read" : "Show what it read"}
      </button>

      {showSources && (
        <div className="mt-3 space-y-3 border-l-2 border-raised pl-3">
          {result.fields.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Filled from the summary
              </p>
              <ul className="mt-1 space-y-1.5">
                {result.fields.map((f) => (
                  <li key={f.name} className="text-xs">
                    <span className="text-bone">
                      {f.name.replace(/_/g, " ")}: <strong>{String(f.value)}</strong>
                    </span>
                    {/* The quote, verbatim. It was checked against the document
                        server-side before it got here, but showing it is what
                        makes the check meaningful to the person reading. */}
                    <span className="ml-1 text-muted">— &ldquo;{f.source}&rdquo;</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.rejected.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-amber/90">
                Proposed and refused
              </p>
              <p className="mt-0.5 text-xs text-muted">
                It suggested these and they were rejected — most often because the
                sentence it quoted was not actually in your summary.
              </p>
              <ul className="mt-1 space-y-0.5">
                {result.rejected.map((r, i) => (
                  <li key={i} className="text-xs text-muted">
                    {r.name}
                    {r.value !== undefined && <> = {String(r.value)}</>} — {r.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.not_found.length > 0 && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Not in the summary — left blank for you
              </p>
              <p className="mt-1 text-xs text-muted">
                {result.not_found.map((n) => n.replace(/_/g, " ")).join(" · ")}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
