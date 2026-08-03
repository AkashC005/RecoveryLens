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
                        String, create_engine)
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


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    patient_ref = Column(String, index=True, nullable=True)
    caregiver_contact = Column(String, nullable=True)
    consent_recorded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

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

    patient = relationship("Patient", back_populates="check_ins")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
