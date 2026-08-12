/**
 * RecoveryLens — SignIn
 *
 * Two forms in one component, because which one you need is a fact about the
 * server rather than a choice the user makes: if no account exists anywhere, the
 * only sensible action is to create the first one, and if one does, the only
 * sensible action is to sign in.
 *
 * `GET /api/auth/status` returns exactly one boolean for this. It deliberately
 * does not report a user count, an email, or an organisation name — an
 * unauthenticated endpoint that names the hospital using the software has told a
 * stranger something they should have had to earn.
 *
 * There is no "continue without signing in". Every other feature in this codebase
 * is off by default and degrades gracefully, which is right for a model that may
 * be unavailable and wrong for access control: a bypass would get used once
 * during a demo and never removed.
 */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Me } from "../lib/api";

export default function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [firstRun, setFirstRun] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organisation, setOrganisation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.authStatus()
      .then((s) => setFirstRun(s.bootstrap_available))
      .catch(() => setError("Cannot reach the API. Is the server running?"));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (firstRun) {
        const r = await api.bootstrap(email, password, organisation);
        if (r.adopted_existing_patients > 0) {
          // Said out loud rather than done quietly. Patients created before
          // authentication existed carry no organisation, so they are invisible
          // to every scoped query until adopted — and someone upgrading would
          // otherwise see an empty patient list and assume data loss.
          setNote(`${r.adopted_existing_patients} existing patient record(s) ` +
                  `moved into ${organisation}.`);
        }
      } else {
        await api.login(email, password);
      }
      onSignedIn(await api.me());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  }

  if (firstRun === null && !error)
    return <p className="text-sm text-muted">Connecting…</p>;

  return (
    <div className="mx-auto max-w-md">
      <h2 className="text-xl font-semibold text-bone">
        {firstRun ? "Create the first account" : "Sign in"}
      </h2>
      <p className="mt-1 mb-6 text-sm text-muted">
        {firstRun
          ? "No account exists yet. This creates your organisation and the first " +
            "clinician login, and then stops being available."
          : "Patient records are only visible to clinicians in your organisation."}
      </p>

      <form onSubmit={submit} className="card space-y-4 p-5">
        {firstRun && (
          <div>
            <label className="field-label" htmlFor="org">Organisation</label>
            <input
              id="org"
              className="field-input"
              value={organisation}
              onChange={(e) => setOrganisation(e.target.value)}
              placeholder="e.g. Apollo Stroke Unit"
              required
            />
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
            autoComplete={firstRun ? "new-password" : "current-password"}
            className="field-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={firstRun ? 12 : undefined}
          />
          {firstRun && (
            <p className="mt-1 text-xs text-muted">
              At least 12 characters. A passphrase is easier to remember and
              harder to guess than a short complicated one.
            </p>
          )}
        </div>

        {error && <p className="text-sm text-signal">{error}</p>}
        {note && <p className="text-sm text-teal">{note}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md border border-teal-dim px-5 py-2.5 text-sm
                     text-teal disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Working…" : firstRun ? "Create account" : "Sign in"}
        </button>
      </form>

      <p className="mt-4 text-xs text-muted/80">
        Carers do not sign in. They answer their own check-in through a link, which
        opens exactly one check-in and nothing else.
      </p>
    </div>
  );
}
