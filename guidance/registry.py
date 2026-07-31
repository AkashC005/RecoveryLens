"""
RecoveryLens — guidance/registry.py
===================================
Deterministic, cited guidance lookup.

Why this is not a vector store
------------------------------
`Predictor._guidance()` emits a closed set of nine trigger keys derived from risk
tiers and deficit flags. Mapping nine known keys to nine known documents is a
dictionary, not a retrieval problem. Embedding them would add ~2GB of torch to a
512MB deployment target and introduce retrieval error into patient-facing
clinical content in exchange for nothing.

The genuine retrieval surface — free-text clinician questions — lives in
`guidance/retrieval.py`. Keep the two separate. This module must never generate
text; it can only return excerpts that a human placed in corpus.json.

Validation runs at import
-------------------------
A guidance layer that silently returns nothing for a trigger is worse than one
that crashes, because the failure only shows up when a judge clicks the chip.
`validate()` runs on import and raises on:
  - a trigger in corpus.json citing an unknown source_id
  - an entry missing a required field
  - an excerpt longer than MAX_EXCERPT_WORDS (copyright guard)
  - a 'covered' trigger with zero entries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent

# NICE content is NICE copyright and their terms prohibit redistribution. We rely
# on short attributed quotation plus a deep link. This cap is the mechanical
# enforcement of that policy — raise it and you are making a legal decision, not
# an engineering one.
MAX_EXCERPT_WORDS = 60

REQUIRED_ENTRY_FIELDS = ("id", "source_id", "section", "excerpt", "url")

# The canonical trigger set. Must stay in step with Predictor._guidance().
# `assert_matches_predictor()` is called at API startup to enforce that.
CANONICAL_TRIGGERS = frozenset({
    "rehabilitation_referral",
    "adherence_support",
    "bleeding_warning_signs",
    "close_monitoring",
    "speech_and_swallowing",
    "arm_rehabilitation",
    "mobility_and_falls",
    "visual_field_safety",
    "general_recovery",
})


class GuidanceError(RuntimeError):
    """Corpus is structurally invalid. Always fatal — never degrade quietly."""


class UnknownTrigger(KeyError):
    """Predictor emitted a trigger the corpus does not cover."""


@dataclass(frozen=True)
class Source:
    id: str
    tier: str
    title: str
    short_title: str
    publisher: str
    url: str
    published: str = ""
    retrieved: str = ""
    jurisdiction: str = ""
    scope_caveat: str = ""
    licence_note: str = ""

    @property
    def is_primary(self) -> bool:
        return self.tier == "primary"


@dataclass
class Registry:
    sources: dict[str, Source] = field(default_factory=dict)
    triggers: dict[str, dict] = field(default_factory=dict)
    rejected: dict[str, dict] = field(default_factory=dict)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, base: Path = HERE) -> "Registry":
        raw_sources = json.loads((base / "sources.json").read_text(encoding="utf-8"))
        raw_corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))

        sources = {
            sid: Source(**{k: v for k, v in s.items() if k in Source.__annotations__})
            for sid, s in raw_sources["sources"].items()
        }
        reg = cls(
            sources=sources,
            triggers=raw_corpus["triggers"],
            rejected=raw_sources.get("rejected_sources", {}),
        )
        reg.validate()
        return reg

    # --------------------------------------------------------------- validation
    def validate(self) -> None:
        problems: list[str] = []

        corpus_keys = set(self.triggers)
        missing = CANONICAL_TRIGGERS - corpus_keys
        extra = corpus_keys - CANONICAL_TRIGGERS
        if missing:
            problems.append(f"triggers in CANONICAL_TRIGGERS but absent from corpus.json: {sorted(missing)}")
        if extra:
            problems.append(f"triggers in corpus.json but not in CANONICAL_TRIGGERS: {sorted(extra)}")

        for name, block in self.triggers.items():
            status = block.get("status")
            if status not in {"covered", "evidence_gap"}:
                problems.append(f"{name}: status must be 'covered' or 'evidence_gap', got {status!r}")

            entries = block.get("entries", [])
            if status == "covered" and not entries:
                problems.append(f"{name}: marked 'covered' but has no entries")
            if status == "evidence_gap":
                if entries:
                    problems.append(f"{name}: marked 'evidence_gap' but carries entries")
                if not block.get("evidence_note"):
                    problems.append(f"{name}: evidence_gap requires an evidence_note explaining the gap")

            for e in entries:
                for f in REQUIRED_ENTRY_FIELDS:
                    if not e.get(f):
                        problems.append(f"{name}/{e.get('id', '?')}: missing required field {f!r}")
                sid = e.get("source_id")
                if sid not in self.sources:
                    problems.append(f"{name}/{e.get('id', '?')}: unknown source_id {sid!r}")
                words = len(str(e.get("excerpt", "")).split())
                if words > MAX_EXCERPT_WORDS:
                    problems.append(
                        f"{name}/{e.get('id', '?')}: excerpt is {words} words, cap is "
                        f"{MAX_EXCERPT_WORDS} (copyright guard)")

        if problems:
            raise GuidanceError(
                "Guidance corpus failed validation:\n  - " + "\n  - ".join(problems))

    def assert_matches_predictor(self, emitted: set[str]) -> None:
        """Call with the full set of triggers Predictor._guidance() can emit."""
        unknown = emitted - CANONICAL_TRIGGERS
        if unknown:
            raise GuidanceError(
                f"Predictor emits triggers with no corpus entry: {sorted(unknown)}. "
                f"Add them to guidance/corpus.json and CANONICAL_TRIGGERS.")

    # ------------------------------------------------------------------- lookup
    def _hydrate(self, entry: dict) -> dict:
        src = self.sources[entry["source_id"]]
        return {
            "id": entry["id"],
            "section": entry["section"],
            "heading": entry.get("heading", ""),
            "excerpt": entry["excerpt"],
            "caveat": entry.get("caveat"),
            # Retrieval-only prior. Fragments pulled from bulleted lists are
            # legitimate citations but poor answers to a question, so they are
            # damped in ranking without being hidden from the trigger view.
            "retrieval_weight": float(entry.get("retrieval_weight", 1.0)),
            "url": entry["url"],
            "source": {
                "id": src.id,
                "tier": src.tier,
                "short_title": src.short_title,
                "title": src.title,
                "publisher": src.publisher,
                "published": src.published,
                "jurisdiction": src.jurisdiction,
                "retrieved": src.retrieved,
                "scope_caveat": src.scope_caveat,
            },
        }

    def get(self, trigger: str) -> dict:
        """One trigger -> its cited guidance block.

        Entries are ordered primary-source-first. That ordering is the
        localisation decision: where an Indian and a UK guideline both speak to a
        topic, the Indian one leads.
        """
        if trigger not in self.triggers:
            raise UnknownTrigger(trigger)

        block = self.triggers[trigger]
        hydrated = [self._hydrate(e) for e in block.get("entries", [])]
        hydrated.sort(key=lambda e: (0 if e["source"]["tier"] == "primary" else 1, e["id"]))

        return {
            "trigger": trigger,
            "label": block.get("label", trigger.replace("_", " ").title()),
            "status": block["status"],
            "audience": block.get("audience", "clinician"),
            # Explicitly flagged as our words, not a guideline's. The frontend
            # must render this distinctly from `entries`.
            "plain_summary": block.get("plain_summary", ""),
            "plain_summary_is_authored_by_recoverylens": True,
            "evidence_note": block.get("evidence_note"),
            "entries": hydrated,
            "entry_count": len(hydrated),
        }

    def for_assessment(self, triggers: list[str]) -> dict:
        """All triggers from one assessment, plus a provenance summary.

        Unknown triggers are surfaced in `unresolved` rather than dropped. A
        trigger that silently vanishes between the model and the UI is exactly
        the failure this whole module exists to prevent.
        """
        blocks, unresolved = [], []
        for t in triggers:
            try:
                blocks.append(self.get(t))
            except UnknownTrigger:
                unresolved.append(t)

        used = {e["source"]["id"] for b in blocks for e in b["entries"]}
        gaps = [b["trigger"] for b in blocks if b["status"] == "evidence_gap"]

        return {
            "guidance": blocks,
            "unresolved_triggers": unresolved,
            "evidence_gaps": gaps,
            "sources_cited": [
                {
                    "id": s.id, "short_title": s.short_title, "title": s.title,
                    "publisher": s.publisher, "url": s.url, "tier": s.tier,
                    "published": s.published, "jurisdiction": s.jurisdiction,
                    "licence_note": s.licence_note,
                }
                for sid, s in self.sources.items() if sid in used
            ],
            "retrieval_method": "deterministic_lookup",
            "disclaimer": (
                "Guidance is retrieved verbatim from published clinical guidelines and is "
                "not generated. Excerpts are quoted with attribution; follow the source "
                "link for the full recommendation and its context. Advisory only."
            ),
        }

    # -------------------------------------------------------------------- meta
    def coverage_report(self) -> dict:
        covered = [t for t, b in self.triggers.items() if b["status"] == "covered"]
        gaps = [t for t, b in self.triggers.items() if b["status"] == "evidence_gap"]
        return {
            "total_triggers": len(self.triggers),
            "covered": sorted(covered),
            "evidence_gaps": sorted(gaps),
            "coverage": f"{len(covered)}/{len(self.triggers)}",
            "total_entries": sum(len(b.get("entries", [])) for b in self.triggers.values()),
            "sources": [
                {"id": s.id, "short_title": s.short_title, "tier": s.tier,
                 "jurisdiction": s.jurisdiction, "url": s.url}
                for s in self.sources.values()
            ],
            "rejected_sources": [
                {"title": r["title"], "rejected_because": r["rejected_because"]}
                for k, r in self.rejected.items() if k != "_note"
            ],
        }


registry = Registry.load()
