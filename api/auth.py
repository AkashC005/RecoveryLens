"""
RecoveryLens API — auth.py
==========================
Who is allowed to see a patient.

Why this exists at all
----------------------
Until this module, every endpoint was open. `GET /api/patients` returned every
patient in the database to anyone who could reach the URL. On localhost that is
merely untidy; deployed, it is an open clinical database, and no amount of
guidance-citation rigour elsewhere compensates for it.

Three principles, in the order they matter
------------------------------------------
1. **THERE IS NO FLAG THAT DISABLES AUTHENTICATION.** Every other feature in this
   codebase is off by default and degrades gracefully — the right pattern for a
   model that might be unavailable, and the wrong one for access control. A
   `RECOVERYLENS_AUTH=0` would eventually be set in a deployment by someone
   debugging at 2am, and never unset. Anyone may register, but nobody may read a
   patient without a session.

2. **AUTHENTICATION WITHOUT AUTHORISATION IS NOT A FIX.** A login that lets every
   clinician read every hospital's patients is the breach people actually suffer.
   Patients belong to an `Organisation`; queries filter on the caller's org.
   `scoped_patient()` is the only sanctioned way to load one.

   **Registering creates your own organisation.** So by default an account sees
   only what that account entered — a reviewer, a judge and you can each sign up
   and none of you sees the others' patients. Sharing is opt-in, through
   `/api/auth/invite`, which can only add someone to the inviter's OWN
   organisation. There is no route that places a user in another one.

   The trade-off is real and worth naming: a colleague cannot cover for you until
   you invite them. For a hospital ward that is wrong, and the fix is to invite
   the team into one organisation. For a prototype being handed to reviewers,
   private-by-default is the safer error.

3. **CARERS DO NOT LOG IN.** Asking the family of a stroke patient to create an
   account and remember a password, on a phone, while worried, means the check-ins
   do not get answered. A carer reaches exactly one check-in through a token in a
   link — unguessable, single-purpose, and dead once the check-in is answered.

Sessions, not JWTs
------------------
A JWT cannot be revoked before expiry without a denylist, which is a session
table with extra steps. "Log this person out now" has to actually work here. The
token is opaque and random; only its hash is stored, so a database dump does not
hand over live sessions.

The cookie
----------
`HttpOnly`, so JavaScript cannot read the token and an XSS bug cannot exfiltrate
it. `SameSite=Lax` to blunt CSRF on state-changing requests. `Secure` whenever the
request arrives over HTTPS — set from the request rather than hardcoded, because
hardcoding it on breaks local development and hardcoding it off is a downgrade
attack waiting to happen.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import os
import secrets

from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .database import (CheckIn, Organisation, Patient, User, UserSession,
                       get_db, utcnow)

COOKIE_NAME = "rl_session"
SESSION_TTL = timedelta(hours=12)

# scrypt parameters. Deliberately expensive: a stolen hash should cost real time
# to attack. Stored alongside each hash so they can be raised later without
# invalidating existing passwords — see `verify_password`.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16

MIN_PASSWORD_LENGTH = 12


# --------------------------------------------------------------------- hashing
def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash`, all hex. stdlib only — no bcrypt dependency."""
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
                        r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison against a stored hash.

    Parameters are read FROM the stored value rather than from the constants
    above, so raising the cost later does not lock out every existing user.
    """
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=32)
    except Exception:
        # A malformed hash is not an authentication success.
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def _hash_token(token: str) -> str:
    """SHA-256 of a session token. Fast on purpose — the token is 256 bits of
    randomness, so there is nothing to brute-force and no need to slow lookups."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# -------------------------------------------------------------------- sessions
def create_session(db: Session, user: User) -> str:
    """Mint a session and return the RAW token. It is never stored or logged."""
    token = secrets.token_urlsafe(32)
    db.add(UserSession(user_id=user.id, token_hash=_hash_token(token),
                       expires_at=utcnow() + SESSION_TTL))
    user.last_login_at = utcnow()
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    row = (db.query(UserSession)
           .filter(UserSession.token_hash == _hash_token(token)).first())
    if row:
        row.revoked = True
        db.commit()


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="lax",
        # From the request, not hardcoded. Hardcoded on breaks localhost;
        # hardcoded off is a downgrade waiting to happen behind TLS.
        secure=request.url.scheme == "https",
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ---------------------------------------------------------------- dependencies
def current_user(rl_session: str | None = Cookie(default=None),
                 db: Session = Depends(get_db)) -> User:
    """The logged-in clinician, or 401. Use this on every clinician route.

    Expiry and revocation are checked on every request, which is the point of
    keeping sessions server-side.
    """
    if not rl_session:
        raise HTTPException(401, "Not signed in.")

    row = (db.query(UserSession)
           .filter(UserSession.token_hash == _hash_token(rl_session)).first())
    if row is None or row.revoked:
        raise HTTPException(401, "Session is no longer valid. Sign in again.")

    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        from datetime import timezone
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires < utcnow():
        raise HTTPException(401, "Session expired. Sign in again.")

    user = row.user
    if user is None or user.disabled:
        raise HTTPException(403, "This account is disabled.")
    return user


def scoped_patients(user: User, db: Session):
    """The ONLY sanctioned way to query patients.

    Returns a query already filtered to the caller's organisation. Every patient
    read goes through here so that adding an endpoint cannot accidentally omit the
    filter — the unscoped query is not the convenient one.
    """
    return db.query(Patient).filter(Patient.organisation_id == user.organisation_id)


def scoped_patient(patient_id: int, user: User, db: Session) -> Patient:
    """One patient, or 404 — never 403.

    A 403 would confirm the patient exists in another organisation, which is a
    patient-existence oracle: an attacker could enumerate ids and learn which
    hospital holds which record. 404 for "not yours" and "not there" is the same
    answer to both questions.
    """
    patient = scoped_patients(user, db).filter(Patient.id == patient_id).first()
    if patient is None:
        raise HTTPException(404, "Patient not found")
    return patient


def scoped_checkin(checkin_id: int, user: User, db: Session) -> CheckIn:
    """One check-in belonging to the caller's organisation, or 404."""
    row = (db.query(CheckIn)
           .join(Patient, CheckIn.patient_id == Patient.id)
           .filter(CheckIn.id == checkin_id,
                   Patient.organisation_id == user.organisation_id).first())
    if row is None:
        raise HTTPException(404, "Check-in not found")
    return row


# ------------------------------------------------------------- caregiver access
def access_token_for(db: Session, checkin: CheckIn) -> str:
    """Mint the carer's link token for this check-in if it has none.

    Minted on demand rather than backfilled: rows created before this feature
    existed still work, and a token that is never needed is never created.
    """
    if not checkin.access_token:
        checkin.access_token = secrets.token_urlsafe(24)
        db.commit()
    return checkin.access_token


def checkin_by_access_token(token: str, db: Session) -> CheckIn:
    """Resolve a carer's token to exactly one check-in.

    Scoped as narrowly as a credential can be: one check-in, no patient list, no
    other check-in, and nothing at all once it has been answered.
    """
    if not token:
        raise HTTPException(401, "This link is missing its access code.")
    row = db.query(CheckIn).filter(CheckIn.access_token == token).first()
    if row is None:
        raise HTTPException(404, "This link is not valid.")
    if row.completed_at is not None:
        raise HTTPException(409, "This check-in has already been answered.")
    return row


def caregiver_or_clinician_checkin(
        checkin_id: int, request: Request, db: Session) -> CheckIn:
    """Allow either the carer holding this check-in's token, or a clinician.

    Both paths exist because both are real: the carer answers from a link, and a
    clinician demonstrating or assisting works from a session. The carer's token
    must match THIS check-in — holding a token for check-in 4 grants nothing about
    check-in 5.
    """
    token = (request.query_params.get("token")
             or request.headers.get("x-checkin-token") or "")
    if token:
        row = checkin_by_access_token(token, db)
        if row.id != checkin_id:
            raise HTTPException(403, "That access code is for a different check-in.")
        return row

    user = current_user(request.cookies.get(COOKIE_NAME), db)
    return scoped_checkin(checkin_id, user, db)


# -------------------------------------------------------------------- bootstrap
def is_first_user(db: Session) -> bool:
    """True while no user exists. Used only to decide whether to adopt orphans."""
    return db.query(User).count() == 0


def signup_code_required() -> str:
    """An optional shared code for registration. Empty means open registration.

    Off by default, because the immediate need is for a reviewer to create their
    own account without anyone provisioning it. But open registration on a public
    URL means anyone who finds it gets a workspace, so this exists for the moment
    that becomes a problem — set RECOVERYLENS_SIGNUP_CODE and only people you have
    given the code to can register.

    It is not a security boundary. It is a speed bump, and it is named honestly as
    one: it stops casual signups, not anyone determined.
    """
    return os.getenv("RECOVERYLENS_SIGNUP_CODE", "").strip()


def create_user(db: Session, *, email: str, password: str, organisation: str,
                full_name: str = "", org_id: int | None = None) -> User:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email address is required.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with that email already exists.")

    if org_id is None:
        org = Organisation(name=organisation.strip() or f"{email}'s workspace")
        db.add(org)
        db.commit()
        db.refresh(org)
        org_id = org.id

    user = User(organisation_id=org_id, email=email,
                password_hash=hash_password(password), full_name=full_name.strip())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def dev_login_hint() -> str:
    """Printed at startup when no account exists, so a fresh clone is not a
    locked door with no key and no message."""
    return ("No clinician account exists yet. Open the app and choose "
            "'Create an account', or:\n"
            "    curl -X POST http://localhost:8000/api/auth/signup \\\n"
            "      -H 'Content-Type: application/json' \\\n"
            "      -d '{\"email\":\"you@example.com\",\"password\":\"choose-a-long-one\","
            "\"organisation\":\"Your hospital\"}'")
