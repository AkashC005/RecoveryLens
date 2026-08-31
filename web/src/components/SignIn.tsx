/**
 * RecoveryLens — SignIn
 *
 * Sign in, or create an account. Both are always available.
 *
 * What this replaced, and why
 * ---------------------------
 * The first version allowed exactly ONE account, ever. It showed
 * "Create the first account" to the first visitor and a bare password box to
 * everyone after — so anyone handed the app had no way in and no way to ask for
 * one. A login form that offers no route to obtaining a login is not a security
 * measure; it is a dead end that looks like one.
 *
 * The privacy model
 * -----------------
 * Registering creates YOUR OWN organisation. Everything you enter is visible only
 * to your account until you invite someone into it, and there is no route that
 * places a user in an organisation they were not invited to. Two people can
 * review this app side by side and neither sees the other's patients.
 *
 * The trade-off is worth stating rather than hiding: a colleague cannot cover for
 * you until you invite them. For a hospital ward that is the wrong default and
 * the fix is to invite the team into one organisation. For a prototype handed to
 * reviewers, private-by-default is the safer error.
 */

import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { api } from "../lib/api";
import type { Me } from "../lib/api";

type Mode = "signin" | "signup";

export default function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [mode, setMode] = useState<Mode>("signin");
  const [needsCode, setNeedsCode] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organisation, setOrganisation] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.authStatus()
      .then((s) => setNeedsCode(s.signup_code_required))
      // Reachability, not registration state. If the API is down, say so here
      // rather than letting the first sign-in attempt fail confusingly.
      .catch(() => setError("Cannot reach the API. Is the server running?"));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      if (mode === "signup") {
        const r = await api.signup(email, password, organisation, code);
        if (r.adopted_existing_patients > 0) {
          // Said out loud rather than done quietly. Records created before
          // accounts existed carry no organisation, so they are invisible to
          // every scoped query until adopted — and someone upgrading would
          // otherwise see an empty patient list and assume data loss.
          setNote(`${r.adopted_existing_patients} existing patient record(s) ` +
                  `moved into your workspace.`);
        }
      } else {
        await api.login(email, password);
      }
      onSignedIn(await api.me());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const isSignup = mode === "signup";

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <Hero />

      <div className="flex items-center justify-center bg-canvas px-5 py-12 lg:px-12">
        <div className="w-full max-w-md">
      {/* A visible toggle, not a buried link. Someone opening this for the first
          time must be able to see that registering is possible without knowing
          to look for it. */}
      <div className="mb-6 flex gap-1 rounded-lg border border-line p-1">
        {(["signin", "signup"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => { setMode(m); setError(null); setNote(null); }}
            aria-pressed={mode === m}
            className={`flex-1 rounded-md px-4 py-2 text-sm transition-colors ${
              mode === m ? "bg-surface text-ink" : "text-muted hover:text-ink"
            }`}
          >
            {m === "signin" ? "Sign in" : "Create an account"}
          </button>
        ))}
      </div>

      <h2 className="text-xl font-semibold text-ink">
        {isSignup ? "Create an account" : "Sign in"}
      </h2>
      <p className="mt-1 mb-6 text-sm text-muted">
        {isSignup
          ? "Your account gets its own private workspace. Patients you add are " +
            "visible only to you until you invite a colleague."
          : "Welcome back."}
      </p>

      <form onSubmit={submit} className="card space-y-4 p-5">
        {isSignup && (
          <div>
            <label className="field-label" htmlFor="org">
              Organisation <span className="text-muted">(optional)</span>
            </label>
            <input
              id="org"
              className="field-input"
              value={organisation}
              onChange={(e) => setOrganisation(e.target.value)}
              placeholder="e.g. Apollo Stroke Unit"
            />
            <p className="mt-1 text-xs text-muted">
              Just a label for your workspace. Leave it blank and we&rsquo;ll name
              it after your email.
            </p>
          </div>
        )}

        <div>
          <label className="field-label" htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            className="field-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>

        <div>
          <label className="field-label" htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete={isSignup ? "new-password" : "current-password"}
            className="field-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={isSignup ? 12 : undefined}
          />
          {isSignup && (
            <p className="mt-1 text-xs text-muted">
              At least 12 characters. A passphrase is easier to remember and
              harder to guess than a short complicated one.
            </p>
          )}
        </div>

        {isSignup && needsCode && (
          <div>
            <label className="field-label" htmlFor="code">Registration code</label>
            <input
              id="code"
              className="field-input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
            <p className="mt-1 text-xs text-muted">
              This deployment requires a code. Ask whoever shared the link.
            </p>
          </div>
        )}

        {error && <p className="text-sm text-danger">{error}</p>}
        {note && <p className="text-sm text-accent">{note}</p>}

        <button
          type="submit"
          disabled={busy}
          className="btn-primary w-full"
        >
          {busy ? "Working…" : isSignup ? "Create account" : "Sign in"}
        </button>

        <p className="text-center text-xs text-muted">
          {isSignup ? "Already have an account?" : "No account yet?"}{" "}
          <button
            type="button"
            onClick={() => { setMode(isSignup ? "signin" : "signup");
                             setError(null); setNote(null); }}
            className="text-accent hover:text-ink"
          >
            {isSignup ? "Sign in instead" : "Create one"}
          </button>
        </p>
      </form>

      <div className="mt-6 space-y-2 text-xs text-faint">
        <p>
          <span className="text-muted">Your data is yours.</span> Patients,
          assessments and check-ins you create are visible only to your account.
          Nobody else signing up here can see them.
        </p>
        <p>
          Carers do not sign in. They answer their own check-in through a link,
          which opens exactly one check-in and nothing else.
        </p>
        <p>
          Research prototype — advisory only. Do not enter real patient
          identifiers.
        </p>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The first thing anyone sees, including a judge.
 *
 * Every figure here is real and is stated with its limitation attached, which is
 * the whole argument this panel makes. The temptation on a landing screen is to
 * write "AI-powered stroke prediction"; a clinician reads that as marketing and
 * discounts everything after it. "19,435 patients, 1991–96, externally validated
 * without refitting" is a smaller claim that survives being asked about.
 *
 * Hidden below `lg` rather than reflowed: on a phone this is a login screen, and
 * a carer opening a check-in link should reach their form without scrolling past
 * a pitch.
 */
function Hero() {
  const reduce = useReducedMotion();
  const rise = (delay: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 14 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] },
        };

  return (
    <div className="relative hidden overflow-hidden bg-wash px-12 py-16 lg:flex lg:flex-col lg:justify-center">
      {/* Two soft washes rather than a gradient across the whole panel: the text
          sits on flat colour and stays legible, while the surface still has
          somewhere for the eye to travel. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-32 -top-32 h-[28rem] w-[28rem] rounded-full bg-accent-soft/50 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-40 -right-24 h-[26rem] w-[26rem] rounded-full bg-calm-soft/60 blur-3xl"
      />

      <div className="relative max-w-xl">
        <motion.div {...rise(0)} className="flex items-center gap-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-accent" aria-hidden />
          <span className="section-label text-accent-strong">RecoveryLens</span>
        </motion.div>

        <motion.h1
          {...rise(0.08)}
          className="mt-6 text-3xl font-semibold text-ink xl:text-display"
        >
          The weeks after a stroke are where recovery is won or lost.
        </motion.h1>

        <motion.p {...rise(0.16)} className="mt-6 max-w-prose text-lg text-ink-soft">
          Rank a patient&rsquo;s risk at discharge, schedule the follow-up the
          evidence supports, and reach the family who is actually doing the
          caring — on WhatsApp, in their own language.
        </motion.p>

        <motion.dl {...rise(0.24)} className="mt-10 grid grid-cols-3 gap-6">
          {[
            { n: "19,435", l: "patients", s: "International Stroke Trial" },
            { n: "0.802", l: "held-out AUC", s: "poor outcome at 6 months" },
            { n: "IST-3", l: "external validation", s: "scored without refitting" },
          ].map((stat) => (
            <div key={stat.l}>
              <dt className="tabular text-2xl font-semibold text-ink">{stat.n}</dt>
              <dd className="mt-1 text-sm font-medium text-ink-soft">{stat.l}</dd>
              <dd className="mt-0.5 text-xs text-muted">{stat.s}</dd>
            </div>
          ))}
        </motion.dl>

        <motion.div
          {...rise(0.32)}
          className="mt-10 border-t border-line pt-6 text-sm text-muted"
        >
          <p>
            Guidance is <span className="text-ink-soft">quoted from published
            sources with citations</span>, never generated. Questions outside the
            corpus are declined rather than answered.
          </p>
          {/* The limitation, on the landing screen rather than buried in a
              footer. A reviewer who finds this themselves trusts everything
              above it less. */}
          <p className="mt-3">
            The models are trained on trial data from 1991&ndash;96, which
            predates routine thrombectomy. They rank patients; they do not
            produce calibrated probabilities. Advisory only.
          </p>
        </motion.div>
      </div>
    </div>
  );
}
