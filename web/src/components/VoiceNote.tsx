/**
 * RecoveryLens — VoiceNote
 *
 * Record a spoken note, read it back, and only then let it become an answer.
 *
 * Why a browser recorder at all
 * ----------------------------
 * The real input is a carer sending a WhatsApp voice note, not someone sitting at
 * a laptop. But the voice pipeline had NO user interface of any kind — it was
 * reachable only by sending audio to a Twilio number — so it could not be shown,
 * tried, or noticed. It went weeks looking like unfinished work because there was
 * nothing to click.
 *
 * This is not a second implementation. It posts to `/api/checkins/{id}/voice`,
 * which calls the same `record_voice_note` the Twilio webhook calls: the same
 * confidence gate, the same negation-loss detection, the same parked-until-
 * confirmed rule. If it ever stops sharing that core, delete it.
 *
 * The read-back is the safety feature
 * ----------------------------------
 * Speech recognition drops negations more readily than anything else. "He can't
 * move his arm" becomes "He can move his arm" — fluent, plausible, and wrong in
 * the reassuring direction, which is the one direction no downstream check can
 * catch. So the transcript is shown back verbatim and nothing is accepted until a
 * human says it is right. There is no confidence score high enough to skip this:
 * a fluent mis-transcription scores well precisely because it is fluent.
 *
 * Abandoning this component mid-flow is safe and deliberate. The transcript stays
 * parked server-side, and the scheduler's `escalate_unconfirmed_voice` sweep tells
 * a clinician that a recording exists which nobody could verify — exactly what
 * happens if a carer ignores the WhatsApp read-back.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { VoiceUpload } from "../lib/api";

type Phase = "idle" | "recording" | "uploading" | "readback" | "unusable";

/** Longer than any useful note, short enough to bound the upload. */
const MAX_SECONDS = 120;

export default function VoiceNote({
  checkInId,
  onConfirmed,
  disabled,
}: {
  checkInId: number | null;
  /** Called with the confirmed transcript, to append to the written notes. */
  onConfirmed: (text: string) => void;
  disabled?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [upload, setUpload] = useState<VoiceUpload | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);

  // Release the microphone on unmount. Without this the browser keeps showing a
  // recording indicator after the user has navigated away, which is alarming and
  // fair enough — something IS still listening.
  useEffect(() => () => stopTracks(), []);

  function stopTracks() {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
    recorder.current = null;
  }

  async function start() {
    if (checkInId == null) return;
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError("This browser cannot record audio. You can type your notes instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunks.current = [];
      mr.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      mr.onstop = () => void send(new Blob(chunks.current, { type: mr.mimeType }));
      recorder.current = mr;
      mr.start();
      setSeconds(0);
      setPhase("recording");
      timer.current = window.setInterval(
        () => setSeconds((s) => (s + 1 >= MAX_SECONDS ? (stop(), s) : s + 1)),
        1000,
      );
    } catch {
      // Almost always a denied permission prompt. Typing is always available, so
      // this is a dead end for one input method rather than for the check-in.
      setError("Could not use the microphone. You can type your notes instead.");
      setPhase("idle");
    }
  }

  function stop() {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    recorder.current?.state === "recording" && recorder.current.stop();
    setPhase("uploading");
  }

  async function send(blob: Blob) {
    stopTracks();
    if (checkInId == null) return;
    try {
      const result = await api.submitVoiceNote(checkInId, blob);
      setUpload(result);
      setPhase(result.usable ? "readback" : "unusable");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the recording");
      setPhase("idle");
    }
  }

  async function resolve(confirmed: boolean) {
    if (checkInId == null) return;
    try {
      const r = await api.confirmVoiceNote(checkInId, confirmed);
      if (r.confirmed && r.transcript) onConfirmed(r.transcript);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save that");
    } finally {
      setUpload(null);
      setPhase("idle");
    }
  }

  const canRecord = checkInId != null && !disabled;

  return (
    <div className="card p-4">
      <p className="text-base text-bone">Or say it out loud</p>
      <p className="mt-0.5 text-sm text-muted">
        Speak instead of typing. We&rsquo;ll show you what we heard and ask you to
        check it before anything is sent.
      </p>

      {/* ------------------------------------------------------------- idle */}
      {phase === "idle" && (
        <button
          type="button"
          onClick={start}
          disabled={!canRecord}
          className="mt-3 rounded-md border border-raised px-4 py-2 text-sm text-bone
                     transition-colors hover:border-teal-dim
                     disabled:cursor-not-allowed disabled:opacity-40"
        >
          Start recording
        </button>
      )}

      {/* -------------------------------------------------------- recording */}
      {phase === "recording" && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-2 text-sm text-signal">
            <span className="h-2 w-2 animate-pulse rounded-full bg-signal" aria-hidden />
            Recording
          </span>
          <span className="font-mono text-xs text-muted">
            {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
            {" / "}
            {MAX_SECONDS / 60}:00
          </span>
          <button
            type="button"
            onClick={stop}
            className="rounded-md border border-teal-dim px-4 py-2 text-sm text-teal"
          >
            Stop
          </button>
        </div>
      )}

      {phase === "uploading" && (
        <p className="mt-3 text-sm text-muted" aria-live="polite">
          Working out what you said…
        </p>
      )}

      {/* --------------------------------------------------------- read-back */}
      {phase === "readback" && upload && (
        <div className="mt-4" aria-live="polite">
          <p className="text-[11px] uppercase tracking-wide text-muted">
            Is this right?
          </p>
          {/* Verbatim, in quotes, visually distinct. The carer is being asked to
              proofread a machine, so it must be obvious that these are the
              machine's words and not theirs. */}
          <blockquote className="mt-2 border-l-2 border-teal-dim pl-3 text-base text-bone">
            &ldquo;{upload.transcript}&rdquo;
          </blockquote>

          {upload.low_confidence && (
            <div className="mt-3 rounded-md border border-amber/40 p-3">
              <p className="text-sm text-amber">
                That recording was hard to hear.
              </p>
              <p className="mt-1 text-xs text-muted">
                We&rsquo;ve still shown you what we think you said. Please read it
                carefully — if any of it is wrong, discard it and record again.
              </p>
            </div>
          )}

          {upload.warnings.length > 0 && (
            <div className="mt-3 rounded-md border border-amber/40 p-3">
              <p className="text-sm text-amber">
                Please read that back carefully.
              </p>
              <p className="mt-1 text-xs text-muted">
                Speech recognition most often drops words like &ldquo;not&rdquo; and
                &ldquo;can&rsquo;t&rdquo;, which flips the meaning to sound better
                than it is. This might be one of those:
              </p>
              <ul className="mt-2 space-y-0.5">
                {upload.warnings.map((w) => (
                  <li key={w} className="text-xs text-amber/90">{w}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => resolve(true)}
              className="rounded-md border border-teal-dim px-4 py-2 text-sm text-teal"
            >
              Yes, that&rsquo;s right
            </button>
            <button
              type="button"
              onClick={() => resolve(false)}
              className="rounded-md border border-raised px-4 py-2 text-sm text-muted
                         hover:text-bone"
            >
              No — discard it
            </button>
          </div>

          <p className="mt-3 text-xs text-muted/80">
            Nothing has been sent yet. If you leave this page without checking it,
            a clinician is told a recording exists that we could not verify.
          </p>
        </div>
      )}

      {/* ---------------------------------------------------------- unusable */}
      {phase === "unusable" && upload && (
        <div className="mt-4" aria-live="polite">
          <p className="text-sm text-bone">{upload.readback}</p>
          {/* Distinguishing "we could not hear you" from "voice was never switched
              on" matters: the second is a configuration problem and telling the
              carer to speak more clearly would be a lie. */}
          {!upload.voice_configured && (
            <p className="mt-2 text-xs text-amber/90">
              Voice transcription is not configured on this server
              (RECOVERYLENS_VOICE), so recordings cannot be read. Typing works.
            </p>
          )}
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => { setUpload(null); setPhase("idle"); }}
              className="rounded-md border border-raised px-4 py-2 text-sm text-bone"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {error && <p className="mt-3 text-sm text-signal">{error}</p>}
    </div>
  );
}
