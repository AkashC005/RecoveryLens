"""
Tests for agent-driven guidance selection.

Run:  pytest tests/test_selector.py -v

The agent chooses WHICH guidance a patient sees. It never writes the guidance
itself. These tests police that boundary, plus the rule floor: topics the
deterministic rules picked must survive whatever the agent decides.
"""

from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guidance.registry import CANONICAL_TRIGGERS  # noqa: E402
from guidance.selector import GuidanceSelector, Selection, SelectionResult  # noqa: E402

RISKS = [
    {"label": "Death or dependency at 6 months", "tier": "elevated",
     "actionability": "actionable", "drivers": [{"factor": "age"}]},
    {"label": "Stopping secondary prevention by 6 months", "tier": "high",
     "actionability": "actionable", "drivers": [{"factor": "aspirin in preceding 3 days"}]},
]

# visuospatial is present and the deterministic rules do not inspect it.
DEFICITS = {
    "deficit_face": "present", "deficit_arm": "present", "deficit_leg": "absent",
    "deficit_speech": "absent", "deficit_visual_field": "absent",
    "deficit_visuospatial": "present", "deficit_brainstem": "absent",
    "deficit_other": "cannot_assess",
}
RULE_TOPICS = ["rehabilitation_referral", "adherence_support",
               "arm_rehabilitation", "general_recovery"]


def _block(kind, **kw):
    b = types.SimpleNamespace(type=kind)
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def _select_block(pairs, i="s1"):
    return _block("tool_use", id=i, name="select_topics", input={
        "selections": [{"topic": t, "rationale": r} for t, r in pairs]})


def selector_with(monkeypatch, script):
    monkeypatch.setenv("RECOVERYLENS_GUIDANCE_AGENT", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    s = GuidanceSelector()
    calls = {"n": 0}

    class Messages:
        def create(self, **kw):
            i = min(calls["n"], len(script) - 1)
            calls["n"] += 1
            return types.SimpleNamespace(content=script[i])

    monkeypatch.setattr(s, "_client",
                        lambda: types.SimpleNamespace(messages=Messages()))
    return s, calls


def run(s):
    return s.select(RISKS, DEFICITS, RULE_TOPICS)


# --------------------------------------------------------------------- floor
def test_rule_topics_always_survive(monkeypatch):
    """Agent picks one unrelated topic; every rule topic must still be present."""
    s, _ = selector_with(monkeypatch, [
        [_select_block([("visual_field_safety", "because")])]])
    out = run(s)
    for t in RULE_TOPICS:
        assert t in out["triggers"], f"rule topic dropped: {t}"


def test_rule_topics_survive_agent_failure(monkeypatch):
    s, _ = selector_with(monkeypatch, [[]])
    monkeypatch.setattr(s, "_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))
    out = run(s)
    assert out["mode"] == "agent_failed"
    assert set(RULE_TOPICS).issubset(set(out["triggers"]))


def test_agent_selecting_nothing_falls_back(monkeypatch):
    s, _ = selector_with(monkeypatch, [[_block("text", text="I decline.")]])
    out = run(s)
    assert out["mode"] == "agent_failed"
    assert set(out["triggers"]) == set(RULE_TOPICS)


def test_dropped_rule_topics_are_labelled_as_rule_sourced(monkeypatch):
    s, _ = selector_with(monkeypatch, [
        [_select_block([("arm_rehabilitation", "arm weakness recorded")])]])
    out = run(s)
    by = {x["topic"]: x for x in out["selections"]}
    assert by["arm_rehabilitation"]["source"] == "agent"
    assert by["adherence_support"]["source"] == "rule"


def test_finalise_is_monotonic_over_arbitrary_agent_output():
    for agent_topics in ([], ["visual_field_safety"], list(CANONICAL_TRIGGERS)):
        r = SelectionResult(
            selections=[Selection(topic=t, rationale="x") for t in agent_topics],
            rule_topics=RULE_TOPICS)
        out = r.finalise()
        assert set(RULE_TOPICS).issubset(set(out["triggers"]))


# --------------------------------------------------------- no fabrication
def test_hallucinated_topic_ids_are_rejected(monkeypatch):
    """An invented topic has no corpus entry and would render as an empty card."""
    s, _ = selector_with(monkeypatch, [
        [_select_block([
            ("cardiac_rehabilitation", "invented"),
            ("nutrition_advice", "also invented"),
            ("arm_rehabilitation", "real"),
        ])]])
    out = run(s)
    assert "cardiac_rehabilitation" not in out["triggers"]
    assert "nutrition_advice" not in out["triggers"]
    assert "arm_rehabilitation" in out["triggers"]


def test_every_selected_topic_exists_in_the_corpus(monkeypatch):
    s, _ = selector_with(monkeypatch, [
        [_select_block([(t, "r") for t in list(CANONICAL_TRIGGERS)[:4]])]])
    for t in run(s)["triggers"]:
        assert t in CANONICAL_TRIGGERS


def test_duplicate_selections_are_collapsed(monkeypatch):
    s, _ = selector_with(monkeypatch, [
        [_select_block([("arm_rehabilitation", "a"), ("arm_rehabilitation", "b")])]])
    triggers = run(s)["triggers"]
    assert triggers.count("arm_rehabilitation") == 1


def test_selector_has_no_tool_that_writes_guidance():
    """The agent selects; the corpus speaks. Nothing here may emit clinical text."""
    from guidance.selector import TOOL_SCHEMAS
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert names == {"list_guidance_topics", "search_guidance", "select_topics"}
    for bad in ("write", "create", "generate", "add_recommendation", "edit"):
        assert not any(bad in n for n in names)


# ------------------------------------------------------------- the blind spot
def test_agent_can_select_a_topic_the_rules_never_reach(monkeypatch):
    """The reason this module exists. The rules inspect 4 of 8 deficits; this
    patient's visuospatial deficit fires nothing."""
    assert "visual_field_safety" not in RULE_TOPICS

    s, _ = selector_with(monkeypatch, [
        [_select_block([
            ("visual_field_safety", "Visuospatial deficit recorded; NG236 covers "
                                    "scanning interventions for neglect."),
            ("arm_rehabilitation", "Arm weakness recorded."),
        ])]])
    out = run(s)
    assert "visual_field_safety" in out["triggers"]
    picked = {x["topic"]: x for x in out["selections"]}
    assert picked["visual_field_safety"]["source"] == "agent"
    assert "isuospatial" in picked["visual_field_safety"]["rationale"]


def test_brief_exposes_all_eight_deficits():
    """If a deficit never reaches the model, the agent has the same blind spot
    as the rules it replaces."""
    brief = GuidanceSelector()._brief(RISKS, DEFICITS)
    for token in ("face", "arm", "visuospatial"):
        assert token in brief
    assert "could not assess" in brief and "other" in brief


# -------------------------------------------------------------- degradation
def test_disabled_selector_is_pure_rules(monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_GUIDANCE_AGENT", raising=False)
    out = GuidanceSelector().select(RISKS, DEFICITS, RULE_TOPICS)
    assert out["mode"] == "rules"
    assert out["triggers"] == RULE_TOPICS
    assert all(x["source"] == "rule" for x in out["selections"])


def test_missing_api_key_falls_back(monkeypatch):
    monkeypatch.setenv("RECOVERYLENS_GUIDANCE_AGENT", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = GuidanceSelector().select(RISKS, DEFICITS, RULE_TOPICS)
    assert out["mode"] == "agent_failed"
    assert set(out["triggers"]) == set(RULE_TOPICS)


def test_loop_is_bounded(monkeypatch):
    looping = [[_block("tool_use", id=f"t{i}", name="list_guidance_topics", input={})]
               for i in range(40)]
    s, calls = selector_with(monkeypatch, looping)
    run(s)
    from guidance.selector import MAX_ITERATIONS
    assert calls["n"] <= MAX_ITERATIONS


def test_tool_trace_is_recorded(monkeypatch):
    s, _ = selector_with(monkeypatch, [
        [_block("tool_use", id="a", name="list_guidance_topics", input={})],
        [_block("tool_use", id="b", name="search_guidance", input={"query": "neglect"})],
        [_select_block([("arm_rehabilitation", "r")])],
    ])
    names = [c["name"] for c in run(s)["tool_calls"]]
    assert names == ["list_guidance_topics", "search_guidance", "select_topics"]


def test_search_tool_returns_real_corpus_excerpts():
    hits = GuidanceSelector()._search("wrist splints upper limb")
    assert hits and "excerpt" in hits[0]
    assert hits[0]["topic"] in CANONICAL_TRIGGERS
