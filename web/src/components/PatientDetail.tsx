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
  AssessmentRequest,
  CheckInRecord,
  MessagingState,
  PatientDetail as Detail,
  RiskResult,
  SendResult,
  Tier,
} from "../lib/api";
import AgentTrace from "./AgentTrace";

// --------------------------------------------------------------------- pieces

function MessagingBanner({
  m,
  patientId,
  onCleared,
}: {
  m: MessagingState;
  patientId: number;
  onCleared: () => void;
}) {
  // Three states worth distinguishing, because the actions differ completely.
  const tone = m.opted_out
    ? "border-danger/50"
    : m.can_send
      ? "border-accent-soft"
      : "border-warn/40";

  return (
    <section className={`card border ${tone} p-4`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-medium text-ink">Follow-up delivery</h3>
        <span
          className={`text-xs ${
            m.can_send ? "text-accent" : m.opted_out ? "text-danger" : "text-warn"
          }`}
        >
          {m.can_send ? "Messages can be sent" : "Messages are not being sent"}
        </span>
      </div>

      {!m.can_send && m.blocked_reason && (
        <p className="mt-2 text-sm text-ink">{m.blocked_reason}</p>
      )}

      <dl className="mt-3 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted">Carer contact</dt>
          <dd className="font-mono text-ink">
            {m.caregiver_contact_on_file
              ? (m.contact_hint ?? "on file")
              : "none on file"}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Consent recorded</dt>
          <dd className={m.consent_recorded ? "text-ink" : "text-warn"}>
            {m.consent_recorded ? "Yes" : "No — nothing will be sent"}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Last heard from carer</dt>
          <dd className="text-ink">
            {m.last_inbound_at
              ? new Date(m.last_inbound_at).toLocaleString()
              : "never"}
          </dd>
        </div>
        <div>
          <dt className="text-muted">WhatsApp 24-hour window</dt>
          <dd className={m.whatsapp_window_open ? "text-accent" : "text-muted"}>
            {m.whatsapp_window_open ? "Open" : "Closed"}
          </dd>
        </div>
      </dl>

      {/* Stated rather than hidden: this constraint is Meta's, applies however
          well the app is built, and a reviewer who discovers it themselves
          trusts the rest of the screen less. */}
      <p className="mt-3 border-t border-line pt-2 text-xs text-muted">
        {m.whatsapp_window_note}
      </p>

      {m.opted_out && (
        <div className="mt-2">
          <p className="text-xs text-danger">
            Opted out
            {m.opted_out_at && ` on ${new Date(m.opted_out_at).toLocaleString()}`}.
            This outranks consent — nothing is sent while it stands.
          </p>
          <ClearOptOut patientId={patientId} onCleared={onCleared} />
        </div>
      )}

      {/* The override, permanently. `opted_out` false with `opted_out_at` set is
          not the same as a patient who never objected, and the screen must not
          let those two look alike — that is the whole reason the timestamp is
          kept when the flag is cleared. */}
      {!m.opted_out && m.opted_out_at && (
        <p className="mt-2 text-xs text-warn">
          This carer opted out on{" "}
          {new Date(m.opted_out_at).toLocaleString()} and the opt-out was
          overridden
          {m.opt_out_cleared_by && ` by ${m.opt_out_cleared_by}`}
          {m.opt_out_cleared_at &&
            ` on ${new Date(m.opt_out_cleared_at).toLocaleString()}`}
          {m.opt_out_cleared_reason && (
            <>
              {" — "}
              <span className="text-ink">“{m.opt_out_cleared_reason}”</span>
            </>
          )}
        </p>
      )}
    </section>
  );
}

/** Override a carer's withdrawal, with a typed justification.
 *
 * Two-step by design. The reason field is not shown until the clinician has
 * asked for it, and the button says what the action is rather than something
 * neutral like "Manage" — a single click that resumes messaging to a family who
 * asked us to stop is not an interaction this should make easy.
 *
 * The minimum length is enforced by the server and mirrored here only so the
 * button can be disabled before the round trip. The server's rule is the one
 * that counts.
 */
function ClearOptOut({
  patientId,
  onCleared,
}: {
  patientId: number;
  onCleared: () => void;
}) {
  const MIN_REASON = 15;
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-1 text-xs text-muted underline hover:text-ink"
      >
        This opt-out was a mistake
      </button>
    );
  }

  return (
    <div className="mt-2 rounded-md border border-danger/40 p-3">
      <p className="text-xs text-ink">
        Overriding a carer&rsquo;s withdrawal. Your name, the time and this
        reason are stored on the patient record permanently.
      </p>
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={2}
        placeholder="Why is it right to resume messaging this family?"
        className="field-input mt-2 w-full"
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={reason.trim().length < MIN_REASON || busy}
          onClick={() => {
            setBusy(true);
            setError(null);
            api
              .clearOptOut(patientId, reason.trim())
              .then(() => {
                setOpen(false);
                setReason("");
                onCleared();
              })
              .catch((e) => setError(String(e)))
              .finally(() => setBusy(false));
          }}
          className="text-xs text-danger disabled:text-muted"
        >
          {busy ? "Recording…" : "Clear the opt-out"}
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            setReason("");
            setError(null);
          }}
          className="text-xs text-muted hover:text-ink"
        >
          Cancel
        </button>
        {reason.trim().length > 0 && reason.trim().length < MIN_REASON && (
          <span className="text-2xs text-muted">
            {MIN_REASON - reason.trim().length} more characters
          </span>
        )}
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </div>
  );
}

function RiskGrid({ risks }: { risks: RiskResult[] }) {
  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {risks.map((r) => {
        const s = TIER_STYLES[r.tier] ?? TIER_STYLES.low;
        return (
          <li key={r.outcome} className="rounded-md border border-line p-3">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 shrink-0 rounded-full ${s.dot}`} aria-hidden />
              <p className="min-w-0 flex-1 truncate text-sm text-ink">{r.label}</p>
              <span className={`text-xs ${s.text}`}>
                {TIER_LABEL[r.tier] ?? r.tier}
              </span>
            </div>
            {/* Percentile, not probability. The models are from 1991-96 trial
                data; the rank is transportable and the absolute number is not,
                which IST-3 validation showed empirically. */}
            <p className="mt-1 font-mono text-2xs text-muted">
              {r.percentile}th percentile · {r.actionability}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

/** Send one check-in, and report honestly which of three things happened.
 *
 * Until this existed the only way to send a check-in was `scripts/send_checkin.py`,
 * so the messaging layer — consent gate, opt-out, rate limit, translation, audio —
 * was unreachable from the product it belongs to.
 *
 * The button is NEVER disabled from `can_send`, and that is deliberate. Two of
 * the four refusal reasons are permanent (opted out, no consent) but the rate
 * limit expires, and `can_send` was computed when the page loaded. Disabling on
 * a stale value would lock a clinician out of a send the server would now allow.
 * So the gate stays the single authority: click, and show what it decided.
 *
 * `can_send` is still used — as a warning before the click, not as a lock.
 */
function SendControl({
  c,
  m,
  onSent,
}: {
  c: CheckInRecord;
  m: MessagingState;
  onSent: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SendResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  function send() {
    setBusy(true);
    setFailed(null);
    setResult(null);
    api
      .sendCheckIn(c.id)
      .then((r) => {
        setResult(r);
        // Only a real send changes the record. Re-fetch so the status badge
        // stops saying "Not sent" while the carer's phone is buzzing.
        if (r.sent) onSent();
      })
      .catch((e) => setFailed(String(e)))
      .finally(() => setBusy(false));
  }

  const language = result?.translation?.language;

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button
          type="button"
          onClick={send}
          disabled={busy}
          className="text-xs text-accent disabled:text-muted"
        >
          {busy ? "Sending…" : c.sent_at ? "Send again" : "Send now"}
        </button>

        {/* A warning, not a lock — see the note above. */}
        {!m.can_send && m.blocked_reason && !result && (
          <span className="text-2xs text-warn">
            Likely to be refused: {m.blocked_reason}
          </span>
        )}
      </div>

      {/* Sent. */}
      {result?.sent && (
        <div className="mt-1.5 text-xs">
          <p className="text-accent">
            Sent{result.channel ? ` via ${result.channel}` : ""}
            {language && language !== "en" && ` · in ${language}`}
            {!result.audio && " · text only, no audio"}
          </p>
          {result.preview && (
            <blockquote className="mt-1 border-l-2 border-accent-soft pl-3 text-muted">
              {result.preview}
            </blockquote>
          )}
        </div>
      )}

      {/* Refused by policy. Amber, not red: the system did the right thing. */}
      {result && !result.sent && result.reason && (
        <p className="mt-1.5 text-xs text-warn">Not sent — {result.reason}</p>
      )}

      {/* The sender was called and failed. Red: this one is ours to fix. */}
      {result && !result.sent && !result.reason && (
        <p className="mt-1.5 text-xs text-danger">
          Send failed — {result.error || "the provider gave no reason"}
        </p>
      )}

      {failed && <p className="mt-1.5 text-xs text-danger">{failed}</p>}
    </div>
  );
}

function CheckInRow({
  c,
  m,
  onSent,
}: {
  c: CheckInRecord;
  m: MessagingState;
  onSent: () => void;
}) {
  const meta = CHECKIN_STATUS_META[c.status] ?? CHECKIN_STATUS_META.scheduled;
  const urgency = URGENCY_STYLES[c.urgency] ?? URGENCY_STYLES.routine;
  const note = (c.responses?.free_text as string) || "";
  const [link, setLink] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);

  return (
    <li className="border-l-2 border-line pl-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={meta.className}>
          {meta.label}
        </span>
        {c.escalated && (
          <span className={urgency.className}>
            {urgency.label}
          </span>
        )}
        <span className="text-sm text-ink">{c.reason || "Check-in"}</span>
        <span className="ml-auto font-mono text-2xs text-muted">
          {new Date(c.scheduled_for).toLocaleDateString()}
        </span>
      </div>

      <p className="mt-1 text-xs text-muted">{meta.hint}</p>

      {c.escalation_reason && (
        <p className="mt-2 text-sm text-ink">{c.escalation_reason}</p>
      )}

      {note && (
        <blockquote className="mt-2 border-l-2 border-accent-soft pl-3 text-sm text-muted">
          &ldquo;{note}&rdquo;
        </blockquote>
      )}

      <AgentTrace triage={c.triage} labelClosed="How this was triaged" />

      {c.status !== "completed" && <SendControl c={c} m={m} onSent={onSent} />}

      {/* The carer's way in. Issued on demand rather than shown by default,
          because the token IS the credential — printing every one on the page
          would put them in screenshots and screen shares. */}
      {c.status !== "completed" && (
        <div className="mt-2">
          {link ? (
            <p className="break-all font-mono text-xs text-accent">{link}</p>
          ) : (
            <button
              type="button"
              onClick={() =>
                api.checkInLink(c.id)
                  .then((r) => setLink(`${window.location.origin}${r.path}`))
                  .catch((e) => setLinkError(String(e)))
              }
              className="text-xs text-accent"
            >
              Get the carer&rsquo;s link
            </button>
          )}
          {linkError && <p className="text-xs text-danger">{linkError}</p>}
        </div>
      )}
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
        className="text-xs text-accent"
      >
        {open ? "Hide what was entered" : "What was entered at discharge"}
      </button>
      {open && (
        <dl className="mt-2 grid gap-x-6 gap-y-1 border-l-2 border-line pl-3 text-xs sm:grid-cols-2">
          {entries.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
              <dd className="font-mono text-ink">{String(v)}</dd>
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
  onReassess,
}: {
  patientId: number;
  onBack: () => void;
  /** Open the assessment form against this patient, seeded with what was last
   *  recorded. Handed the previous inputs rather than fetching them again so the
   *  form starts from exactly what is on screen. */
  onReassess: (initial: Partial<AssessmentRequest>) => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setError(null);
    api.patient(patientId).then(setD).catch((e) => setError(String(e)));
  }, [patientId]);

  /** Re-read the record without blanking the screen.
   *
   *  Distinct from the effect above, which clears `d` first because a patient
   *  switch should not show the previous patient's data while loading. Here the
   *  patient has not changed and only one check-in has moved, so holding the
   *  current view is correct — and a send that silently emptied the page would
   *  read as a failure. A refresh that fails is left silent for the same reason:
   *  the send succeeded, and that is the fact the clinician needs. */
  function refresh() {
    api.patient(patientId).then(setD).catch(() => {});
  }

  const back = (
    <button onClick={onBack} className="mb-6 text-sm text-accent hover:text-ink">
      ← All patients
    </button>
  );

  if (error) return <div>{back}<p className="text-sm text-danger">{error}</p></div>;
  if (!d) return <div>{back}<p className="text-sm text-muted">Loading…</p></div>;

  const latest = d.assessments[0];
  const risks = latest?.results?.risks ?? [];

  return (
    <div>
      {back}

      <header className="mb-6">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h2 className="text-xl font-semibold text-ink">
            {d.patient_ref || <span className="text-muted">Unlabelled patient</span>}
          </h2>
          {/* Targets this record by id. The alternative — a new assessment
              carrying the same reference — relies on the server matching text,
              which is fine at discharge and wrong once two patients share a
              blank or duplicated reference. */}
          <button
            type="button"
            onClick={() => onReassess(latest?.inputs ?? {})}
            className="text-sm text-accent hover:text-ink"
          >
            Re-assess
          </button>
        </div>
        <p className="mt-1 font-mono text-xs text-muted">
          #{d.id} · first assessed {new Date(d.created_at).toLocaleDateString()} ·{" "}
          {d.assessments.length} assessment{d.assessments.length === 1 ? "" : "s"}
          {d.open_escalations > 0 && (
            <span className="text-danger">
              {" "}· {d.open_escalations} escalation
              {d.open_escalations === 1 ? "" : "s"}
            </span>
          )}
        </p>
      </header>

      <div className="space-y-6">
        <MessagingBanner
          m={d.messaging}
          patientId={d.id}
          onCleared={refresh}
        />

        {risks.length > 0 && (
          <section>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
              <h3 className="text-sm font-medium text-ink">Risk profile</h3>
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
                    className="chip-neutral"
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
            <h3 className="text-sm font-medium text-ink">Follow-up trail</h3>
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
                <CheckInRow key={c.id} c={c} m={d.messaging} onSent={refresh} />
              ))}
            </ul>
          )}
        </section>

        {d.assessments.length > 1 && (
          <section>
            <h3 className="mb-3 text-sm font-medium text-ink">
              Earlier assessments
            </h3>
            <ul className="space-y-3">
              {d.assessments.slice(1).map((a) => (
                <li key={a.id} className="rounded-md border border-line p-3">
                  <p className="text-sm text-ink">
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
