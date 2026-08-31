"""
RecoveryLens API — database.py
==============================
SQLite via SQLAlchemy. No server to run, no ops budget, and the whole database
is a single file you can delete and recreate.

Privacy note: `patient_ref` is your own opaque identifier and `caregiver_contact`
is a phone or email. Do not store patient names here. Once real contact details
are entered, India's DPDP Act applies — consent must be recorded before any
check-in is sent.
"""

from datetime import datetime, timezone
import os

from sqlalchemy import (JSON, Boolean, Column, DateTime, ForeignKey, Integer,
                        LargeBinary, String, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./recoverylens.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Organisation(Base):
    """A tenant. One hospital, one stroke unit, one clinic.

    Patients belong to an organisation and clinicians only ever see their own.
    Without this, "authentication" would mean every logged-in user can read every
    patient in the database — which is authentication without authorisation, and
    is the failure people actually get breached by.
    """
    __tablename__ = "organisations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    users = relationship("User", back_populates="organisation",
                         cascade="all, delete-orphan")


class User(Base):
    """A clinician. Not a patient and not a carer — neither of those log in.

    Carers reach their own check-in through a per-check-in token instead
    (`CheckIn.access_token`). Asking the family of a stroke patient to create an
    account and remember a password, on a phone, while worried, would mean the
    check-ins simply do not get answered.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    organisation_id = Column(Integer, ForeignKey("organisations.id"), index=True,
                             nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    # scrypt, salted per user. See api/auth.py — the format and parameters are
    # stored with the hash so they can be raised later without invalidating
    # everyone's password.
    password_hash = Column(String, nullable=False)
    full_name = Column(String, default="")
    created_at = Column(DateTime, default=utcnow)
    last_login_at = Column(DateTime, nullable=True)
    disabled = Column(Boolean, default=False, index=True)

    organisation = relationship("Organisation", back_populates="users")
    sessions = relationship("UserSession", back_populates="user",
                            cascade="all, delete-orphan")


class UserSession(Base):
    """A server-side session. Deliberately not a JWT.

    A JWT cannot be revoked before it expires without keeping a denylist, which
    is a session table with extra steps. For a system holding clinical data,
    "log this person out right now" has to actually work — so the token is an
    opaque random string and the record here is the authority.

    Only the HASH of the token is stored. A database dump should not hand over
    live sessions.
    """
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, index=True, nullable=False)
    revoked = Column(Boolean, default=False, index=True)

    user = relationship("User", back_populates="sessions")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable so existing rows survive the migration; every new patient gets one
    # from the creating user. Queries filter on it, so a null-org patient is
    # invisible rather than visible to everyone — the safe direction.
    organisation_id = Column(Integer, ForeignKey("organisations.id"), index=True,
                             nullable=True)
    patient_ref = Column(String, index=True, nullable=True)
    caregiver_contact = Column(String, index=True, nullable=True)
    consent_recorded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

    # BCP-47-ish language tag for the carer's messages: "en", "ta", "hi".
    # Clinician-facing text is always English regardless — see guidance/translate.py.
    language = Column(String, default="en")

    # Set when a carer replies STOP. Permanent and outranks consent — an opt-out
    # is a withdrawal, and must not be overridden by an older consent record.
    # Only a human should ever clear this.
    opted_out = Column(Boolean, default=False, index=True)
    opted_out_at = Column(DateTime, nullable=True)

    # A clinician overriding a withdrawal, recorded permanently.
    #
    # `opted_out` going back to False must NEVER make the record look like a
    # patient who simply never opted out. A carer said stop; someone decided that
    # did not count. That decision is auditable or it should not be possible, so
    # these three are written together and `opted_out_at` is deliberately left in
    # place after a clear — the date of the withdrawal outlives the withdrawal.
    #
    # The clinician's EMAIL rather than a user id, on purpose: an audit trail
    # that stops being readable when a user row is deleted is not an audit trail.
    opt_out_cleared_at = Column(DateTime, nullable=True)
    opt_out_cleared_by = Column(String, nullable=True)
    opt_out_cleared_reason = Column(String, nullable=True)

    # Last time this carer sent us anything. WhatsApp only allows free-form
    # messages within 24 hours of it; outside that window, templates only.
    last_inbound_at = Column(DateTime, nullable=True)

    assessments = relationship("Assessment", back_populates="patient",
                               cascade="all, delete-orphan")
    check_ins = relationship("CheckIn", back_populates="patient",
                             cascade="all, delete-orphan")


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True)
    created_at = Column(DateTime, default=utcnow)

    inputs = Column(JSON)            # the request, as submitted
    results = Column(JSON)           # risks, tiers, drivers
    guidance_triggers = Column(JSON)

    patient = relationship("Patient", back_populates="assessments")


class CheckIn(Base):
    __tablename__ = "check_ins"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True)
    scheduled_for = Column(DateTime, index=True)
    sent_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    reason = Column(String)          # why this check-in exists
    responses = Column(JSON, nullable=True)
    escalated = Column(Boolean, default=False, index=True)
    escalation_reason = Column(String, nullable=True)

    # Triage output. `triage` holds the full record: which reasons came from the
    # boolean rules and which from the agent, the urgency, the agent's summary,
    # and the tool trace. Kept separate from `escalation_reason` so a clinician
    # can always see the provenance of a flag — rule or agent — rather than a
    # merged string that hides which is which.
    urgency = Column(String, default="routine", index=True)
    triage = Column(JSON, nullable=True)

    # How a carer reaches THIS check-in without an account. Unguessable, scoped to
    # one check-in, and useless once the check-in is answered. A carer gets a link
    # containing it; they never see another patient's anything.
    #
    # Nullable because existing rows predate it. `access_token_for()` mints one on
    # demand rather than requiring a backfill.
    access_token = Column(String, unique=True, index=True, nullable=True)

    patient = relationship("Patient", back_populates="check_ins")


class MediaAsset(Base):
    """One generated audio file, served from an unguessable expiring URL.

    Twilio cannot hold a session cookie: to attach audio to a WhatsApp message it
    must fetch a publicly reachable URL itself. That is a deliberate hole in the
    authentication boundary, so it is made as narrow as a hole can be:

      - the token is 256 bits of randomness and IS the credential
      - one asset per token; there is no listing route and no enumerable id
      - `expires_at` is short, because Twilio fetches within seconds
      - the spoken text deliberately EXCLUDES the patient reference, so even a
        leaked URL discloses generic guidance rather than who it is about

    Bytes live in the database rather than on disk. Render's free tier has an
    ephemeral filesystem, so a file written before a restart is gone while the row
    pointing at it survives — a dangling reference is worse than a slightly larger
    database. Expiry and purging are then one query rather than a cron job over a
    directory.
    """
    __tablename__ = "media_assets"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    check_in_id = Column(Integer, ForeignKey("check_ins.id"), index=True,
                         nullable=True)
    mime_type = Column(String, default="audio/ogg")
    data = Column(LargeBinary, nullable=False)
    language = Column(String, default="en")
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, index=True, nullable=False)
    fetch_count = Column(Integer, default=0)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _check_schema_drift()


def _add_missing_columns() -> None:
    """Add columns the models have and the database does not.

    `create_all()` creates missing TABLES but never adds columns, so every time
    a model gains a field the existing database breaks. That happened three
    times during development, each costing a wipe.

    Adding a nullable column is the one migration that is genuinely safe to do
    automatically: SQLite supports ALTER TABLE ADD COLUMN, existing rows get
    NULL, and no data moves. Anything else — a dropped column, a changed type, a
    new NOT NULL without a default — is left to `_check_schema_drift()` to
    report, because those need a human decision.

    This is not a substitute for migrations. Before deploying anything holding
    data worth keeping, use alembic.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}

        for column in table.columns:
            if column.name in present:
                continue
            # Refuse anything that cannot be added without a value for the rows
            # already there.
            if not column.nullable and column.default is None and column.server_default is None:
                continue

            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} " \
                  f"{column.type.compile(engine.dialect)}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
            except Exception as exc:
                print(f"[db] could not add {table.name}.{column.name}: "
                      f"{type(exc).__name__}")

    if added:
        print(f"[db] added missing columns: {', '.join(added)}")


def _check_schema_drift() -> None:
    """Fail loudly when the database predates a model change.

    `create_all()` creates missing TABLES but never adds columns to existing
    ones. So adding a field to a model leaves an old SQLite file intact and
    every query against that table then dies with `no such column: x`, thrown
    from deep inside SQLAlchemy at request time — which surfaces in the browser
    as an opaque "Failed to fetch" with the real cause buried under fifty lines
    of middleware traceback.

    Detecting it at startup turns a confusing runtime failure into one line that
    says exactly what to do. Proper migrations (alembic) are the real answer
    before any deployment holding data worth keeping.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    problems: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        actual = {c["name"] for c in inspector.get_columns(table.name)}
        missing = {c.name for c in table.columns} - actual
        if missing:
            problems.append(f"{table.name}: missing {sorted(missing)}")

    if problems:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        raise RuntimeError(
            "Database schema is out of date:\n  - "
            + "\n  - ".join(problems)
            + f"\n\nThe models gained columns that this database does not have. "
              f"It holds demo data only, so the fix is to delete it:\n"
              f"    rm {db_path}\n"
              f"Then restart. The tables are recreated on startup."
        )


def get_db():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
