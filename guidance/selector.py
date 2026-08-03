"""
RecoveryLens — guidance/selector.py
===================================
Agent-driven guidance selection.

What this replaces
------------------
`Predictor._guidance()` decides which guidance a patient needs with a fixed
if/else chain. It works, and it is reproducible, but it has a concrete gap:

    it inspects 4 of the 8 recorded deficits.

deficit_face, deficit_visuospatial, deficit_brainstem and deficit_other fire
nothing at all. A patient with visuospatial neglect gets no guidance about it,
even though NICE NG236 covers scanning interventions for exactly that. The rules
were written against the triggers that existed, and the blind spots are invisible
because a rule that does not fire looks identical to a patient who does not need
it.

An agent reads the whole picture — every deficit, every risk tier, the SHAP
drivers behind those tiers — and decides what matters for THIS patient. It can
also say why, which the if/else never could.

What the agent can and cannot do
--------------------------------
CAN: choose which topics apply, in what order, and explain its reasoning.
CANNOT: write clinical text. Every excerpt still comes verbatim from the corpus,
with its citation. The model selects; the guideline speaks.

That boundary is what keeps the hallucination story intact while moving the
decision to a model. A wrong selection shows a clinician guidance that is
correctly quoted but less relevant — recoverable, and visible in the reasoning.
A wrong generation would put words in NICE's mouth, which is not.

Degradation
-----------
`select()` always returns a usable result. If the agent is disabled, unreachable,
or returns nonsense, it falls back to the deterministic rules and says so in
`mode`. The product never depends on the model being available.

Rule floor
----------
Topics the deterministic rules would have chosen are ALWAYS included, whatever
the agent decides. Same monotonic principle as triage escalation: the agent may
add and reorder, never remove. If the rules say a patient at elevated risk of
stopping their medication needs adherence support, no model output removes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os

from .registry import CANONICAL_TRIGGERS, Registry, registry as default_registry
from .retrieval import CorpusRetriever

MAX_ITERATIONS = 5
MAX_TOKENS = 1500
DEFAULT_MODEL = "claude-sonnet-5"

# Deficits the deterministic rules never look at. Recorded here because the
# whole justification for this module is that these were being dropped.
RULE_BLIND_SPOTS = ["deficit_face", "deficit_visuospatial",
                    "deficit_brainstem", "deficit_other"]


def selector_enabled() -> bool:
    return os.getenv("RECOVERYLENS_GUIDANCE_AGENT", "").strip() in {"1", "true", "yes"}


TOOL_SCHEMAS = [
    {
        "name": "list_guidance_topics",
        "description": (
            "The guidance topics available, with how many cited recommendations "
            "each holds and whether it is a documented evidence gap. Call this "
            "first — you may only select from these."),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_guidance",
        "description": (
            "Search the corpus for recommendations on a clinical topic. Use this "
            "to check whether the corpus actually covers something you are "
            "considering. Returns verbatim excerpts, or nothing if not covered."),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "select_topics",
        "description": (
            "Record your selection. Call once, at the end, then stop."),
        "input_schema": {
            "type": "object",
            "properties": {
                "selections": {
                    "type": "array",
                    "description": "Ordered by clinical priority, most important first.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string",
                                      "description": "Must be a topic id from list_guidance_topics."},
                            "rationale": {"type": "string",
                                          "description": "One sentence: why THIS patient needs this, "
                                                         "referencing their specific findings."},
                        },
                        "required": ["topic", "rationale"],
                    },
                },
            },
            "required": ["selections"],
        },
    },
]


SYSTEM = """\
You are selecting which post-stroke guidance topics apply to one patient, for \
the clinician planning their discharge and follow-up.

You are given: predicted risk tiers for six outcomes, the factors driving each \
prediction, and every neurological deficit recorded at assessment.

How to work:
1. Call list_guidance_topics first. You may only select from those ids.
2. Use search_guidance if you are unsure whether the corpus covers something.
3. Call select_topics once with your ordered selection, then stop.

Selecting well:
- Every recorded deficit deserves consideration. A deficit with no obvious topic \
is still worth noting in your rationale for a related one.
- Risk tiers of 'elevated' or 'high' should almost always produce a topic.
- Order by what matters most for this patient, not by the order given to you.
- Include general_recovery for everyone.
- A topic marked as an evidence gap may still be selected if clinically \
relevant; the product will show the gap honestly.
- Do not select everything. A list of nine topics is the same as no priorities.

Your rationale is shown to a clinician. Make it specific to this patient's \
findings — "arm weakness recorded with elevated 6-month dependency risk" is \
useful, "arm rehabilitation is important" is not.

You are choosing topics, not writing clinical advice. The guideline text is \
supplied separately and verbatim; never paraphrase or invent a recommendation.
"""


@dataclass
class Selection:
    topic: str
    rationale: str
    source: str = "agent"          # agent | rule


@dataclass
class SelectionResult:
    selections: list[Selection] = field(default_factory=list)
    rule_topics: list[str] = field(default_factory=list)
    mode: str = "rules"            # rules | agent | agent_failed
    summary: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None

    def finalise(self) -> dict:
        """Rule-selected topics are always present. The agent adds and orders."""
        chosen = {s.topic: s for s in self.selections}

        # Anything the rules picked that the agent omitted gets appended, marked
        # as rule-sourced so the UI can show it was not the model's idea.
        for t in self.rule_topics:
            if t not in chosen:
                chosen[t] = Selection(
                    topic=t, source="rule",
                    rationale="Selected by the deterministic rules; the agent did "
                              "not include it.")

        ordered = [s for s in self.selections if s.topic in chosen]
        ordered += [s for t, s in chosen.items()
                    if s.source == "rule" and s not in ordered]

        return {
            "triggers": [s.topic for s in ordered],
            "selections": [
                {"topic": s.topic, "rationale": s.rationale, "source": s.source}
                for s in ordered
            ],
            "rule_topics": list(self.rule_topics),
            "mode": self.mode,
            "agent_summary": self.summary,
            "tool_calls": self.tool_calls,
            "agent_error": self.error,
        }


class GuidanceSelector:
    def __init__(self, reg: Registry | None = None,
                 retriever: CorpusRetriever | None = None,
                 model: str | None = None):
        self.registry = reg or default_registry
        self._retriever = retriever
        self.model = model or os.getenv("RECOVERYLENS_LLM_MODEL", DEFAULT_MODEL)

    @property
    def retriever(self) -> CorpusRetriever:
        if self._retriever is None:
            self._retriever = CorpusRetriever(self.registry)
        return self._retriever

    # ------------------------------------------------------------------ tools
    def _list_topics(self) -> list[dict]:
        out = []
        for t in sorted(self.registry.triggers):
            block = self.registry.triggers[t]
            out.append({
                "id": t,
                "label": block.get("label", t),
                "status": block["status"],
                "recommendations": len(block.get("entries", [])),
                "audience": block.get("audience", "clinician"),
            })
        return out

    def _search(self, query: str) -> list[dict]:
        try:
            return [
                {"source": h["source"]["short_title"], "section": h["section"],
                 "topic": h["trigger"], "excerpt": h["excerpt"]}
                for h in self.retriever.search(query, top_k=3)
            ]
        except Exception as exc:
            return [{"error": type(exc).__name__}]

    # ----------------------------------------------------------------- select
    def select(self, risks: list[dict], deficits: dict[str, str],
               rule_topics: list[str]) -> dict:
        result = SelectionResult(rule_topics=list(rule_topics))

        if not selector_enabled():
            result.selections = [
                Selection(topic=t, source="rule",
                          rationale="Selected by the deterministic rules.")
                for t in rule_topics
            ]
            return result.finalise()

        try:
            client = self._client()
        except Exception as exc:
            result.mode = "agent_failed"
            result.error = f"{type(exc).__name__}: {exc}"[:200]
            return result.finalise()

        result.mode = "agent"
        messages = [{"role": "user", "content": self._brief(risks, deficits)}]

        try:
            for _ in range(MAX_ITERATIONS):
                resp = client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS,
                    system=SYSTEM, tools=TOOL_SCHEMAS, messages=messages)

                text = "".join(b.text for b in resp.content
                               if getattr(b, "type", "") == "text").strip()
                if text:
                    result.summary = text

                uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
                if not uses:
                    break

                messages.append({"role": "assistant", "content": resp.content})
                tool_results = []
                done = False

                for block in uses:
                    name = block.name
                    args = block.input or {}
                    self._record(result, name, args)

                    if name == "list_guidance_topics":
                        payload = self._list_topics()
                    elif name == "search_guidance":
                        payload = self._search(str(args.get("query", "")))
                    elif name == "select_topics":
                        payload = self._accept(result, args)
                        done = True
                    else:
                        payload = {"error": f"unknown tool {name!r}"}

                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": json.dumps(payload, default=str)[:6000],
                    })

                messages.append({"role": "user", "content": tool_results})
                if done:
                    break

        except Exception as exc:
            result.mode = "agent_failed"
            result.error = f"{type(exc).__name__}: {exc}"[:200]

        # An agent that produced nothing usable is the same as no agent.
        if not result.selections:
            if result.mode == "agent":
                result.mode = "agent_failed"
                result.error = result.error or "agent selected no topics"
            result.selections = [
                Selection(topic=t, source="rule",
                          rationale="Fell back to the deterministic rules.")
                for t in rule_topics
            ]

        return result.finalise()

    # ---------------------------------------------------------------- helpers
    def _accept(self, result: SelectionResult, args: dict) -> dict:
        """Validate the agent's selection against real topic ids.

        A hallucinated topic id is dropped rather than passed through — there is
        no corpus entry behind it, so it would render as an empty card.
        """
        accepted, rejected = [], []
        for item in args.get("selections", []) or []:
            topic = str(item.get("topic", "")).strip()
            rationale = str(item.get("rationale", "")).strip()
            if topic in CANONICAL_TRIGGERS and topic not in {s.topic for s in accepted}:
                accepted.append(Selection(topic=topic, rationale=rationale))
            else:
                rejected.append(topic)

        result.selections.extend(accepted)
        return {
            "accepted": [s.topic for s in accepted],
            "rejected": rejected,
            "note": ("Rejected ids are not real guidance topics and were dropped."
                     if rejected else "All selections accepted."),
        }

    @staticmethod
    def _record(result: SelectionResult, name: str, args: dict) -> None:
        result.tool_calls.append({"name": name, "arguments": args})

    @staticmethod
    def _brief(risks: list[dict], deficits: dict[str, str]) -> str:
        lines = ["Predicted risks:"]
        for r in risks:
            drivers = ", ".join(d.get("factor", "") for d in r.get("drivers", [])[:3])
            lines.append(
                f"  - {r.get('label')}: {r.get('tier')} "
                f"({r.get('actionability')}) — driven by: {drivers or 'n/a'}")

        lines.append("\nDeficits recorded at assessment:")
        present = [k.replace("deficit_", "").replace("_", " ")
                   for k, v in deficits.items() if v == "present"]
        unassessed = [k.replace("deficit_", "").replace("_", " ")
                      for k, v in deficits.items() if v == "cannot_assess"]
        lines.append(f"  present: {', '.join(present) if present else 'none'}")
        lines.append(f"  could not assess: {', '.join(unassessed) if unassessed else 'none'}")
        lines.append("\nSelect the guidance topics this patient needs.")
        return "\n".join(lines)

    def _client(self):
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic
        return anthropic.Anthropic(api_key=key)


selector = GuidanceSelector()
