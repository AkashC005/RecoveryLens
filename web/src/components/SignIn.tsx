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
    <div className="mx-auto max-w-md">
      {/* A visible toggle, not a buried link. Someone opening this for the first
          time must be able to see that registering is possible without knowing
          to look for it. */}
      <div className="mb-6 flex gap-1 rounded-lg border border-raised p-1">
        {(["signin", "signup"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => { setMode(m); setError(null); setNote(null); }}
            aria-pressed={mode === m}
            className={`flex-1 rounded-md px-4 py-2 text-sm transition-colors ${
              mode === m ? "bg-slate text-bone" : "text-muted hover:text-bone"
            }`}
          >
            {m === "signin" ? "Sign in" : "Create an account"}
          </button>
        ))}
      </div>

      <h2 className="text-xl font-semibold text-bone">
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

        {error && <p className="text-sm text-signal">{error}</p>}
        {note && <p className="text-sm text-teal">{note}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md border border-teal-dim px-5 py-2.5 text-sm
                     text-teal disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Working…" : isSignup ? "Create account" : "Sign in"}
        </button>

        <p className="text-center text-xs text-muted">
          {isSignup ? "Already have an account?" : "No account yet?"}{" "}
          <button
            type="button"
            onClick={() => { setMode(isSignup ? "signin" : "signup");
                             setError(null); setNote(null); }}
            className="text-teal hover:text-bone"
          >
            {isSignup ? "Sign in instead" : "Create one"}
          </button>
        </p>
      </form>

      <div className="mt-6 space-y-2 text-xs text-muted/80">
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
  );
}
