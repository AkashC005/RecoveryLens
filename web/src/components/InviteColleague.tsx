/**
 * RecoveryLens — InviteColleague
 *
 * Adds a colleague to the signed-in clinician's own organisation.
 *
 * Why this screen exists
 * ---------------------
 * Organisation scoping is the whole access-control model: a patient belongs to
 * an organisation, and `scoped_patients()` will not return one from any other.
 * `POST /api/auth/invite` was built to let a second clinician into that
 * organisation, and nothing in the frontend called it — so every account was
 * permanently a team of one, and a registrar joining a ward had no way in.
 *
 * What it is honest about
 * ----------------------
 * The word "invite" is generous. There is no email, no acceptance step and no
 * pending state: the account is created immediately with a password the inviter
 * types. Worse, there is no password-change route yet, so whatever is set here
 * is what that colleague uses indefinitely, and the inviter knows it.
 *
 * That is a real weakness and the UI says so plainly rather than dressing the
 * form up as an invitation. A clinician who believes an email went out will
 * wonder why their colleague never signed in; one who is told they are handing
 * over a password will behave accordingly.
 */

import { useState } from "react";
import { api, MIN_PASSWORD_LENGTH } from "../lib/api";
import type { InvitedUser, Me } from "../lib/api";

export default function InviteColleague({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<InvitedUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const ready = email.trim() !== "" && password.length >= MIN_PASSWORD_LENGTH;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCreated(null);
    api
      .invite(email.trim(), password, fullName.trim())
      .then((u) => {
        setCreated(u);
        // Cleared on success so a second invite cannot silently reuse the first
        // colleague's password because the field still held it.
        setEmail("");
        setPassword("");
        setFullName("");
      })
      .catch((err) => setError(String(err)))
      .finally(() => setBusy(false));
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-xs text-muted hover:text-ink"
        title="Your organisation — click to add a colleague"
      >
        {me.organisation || me.email}
      </button>

      {open && (
        <div className="card absolute right-0 z-20 mt-2 w-80 border border-line p-4 text-left shadow-lg">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-medium text-ink">Add a colleague</h3>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-xs text-muted hover:text-ink"
            >
              Close
            </button>
          </div>

          <p className="mt-1 text-xs text-muted">
            They join <span className="text-ink">{me.organisation || "your organisation"}</span>{" "}
            and can see every patient in it. There is no route into any other
            organisation.
          </p>

          <form onSubmit={submit} className="mt-3 space-y-3">
            <div>
              <label className="field-label" htmlFor="invite-name">
                Name <span className="text-muted">(optional)</span>
              </label>
              <input
                id="invite-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="field-input"
                autoComplete="off"
              />
            </div>

            <div>
              <label className="field-label" htmlFor="invite-email">Email</label>
              <input
                id="invite-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="field-input"
                autoComplete="off"
              />
            </div>

            <div>
              <label className="field-label" htmlFor="invite-password">
                Password you are giving them
              </label>
              <input
                id="invite-password"
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="field-input"
                autoComplete="new-password"
              />
              {tooShort && (
                <p className="mt-1 text-xs text-warn">
                  At least {MIN_PASSWORD_LENGTH} characters.
                </p>
              )}
            </div>

            {/* Said before the button, not after the fact. A clinician who
                thinks an invitation email went out will wait for a colleague who
                was never told anything. */}
            <p className="rounded-md border border-warn/30 p-2 text-xs text-warn">
              No email is sent. This creates the account immediately, and they
              cannot change this password yet — tell them what it is by some
              other means, and treat it as shared until that changes.
            </p>

            <button
              type="submit"
              disabled={!ready || busy}
              className="btn-primary w-full"
            >
              {busy ? "Creating…" : "Create the account"}
            </button>
          </form>

          {created && (
            <p className="mt-3 text-xs text-accent">
              {created.email} can now sign in.
            </p>
          )}
          {error && <p className="mt-3 text-xs text-danger">{error}</p>}
        </div>
      )}
    </div>
  );
}
