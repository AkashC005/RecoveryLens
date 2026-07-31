"""
Tests for the RecoveryLens guidance layer.

Run:  pytest tests/test_guidance.py -v

These are mostly negative tests. The corpus loading correctly proves very little;
what matters is that the guards actually fire when the corpus is wrong, because
those guards are the only thing standing between a well-meaning edit and an
uncited clinical claim reaching a user.
"""

from copy import deepcopy
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guidance.registry import (  # noqa: E402
    CANONICAL_TRIGGERS, GuidanceError, MAX_EXCERPT_WORDS, Registry, UnknownTrigger,
)
from guidance.retrieval import CorpusRetriever  # noqa: E402

CORPUS_DIR = Path(__file__).resolve().parent.parent / "guidance"


@pytest.fixture(scope="module")
def reg() -> Registry:
    return Registry.load()


@pytest.fixture(scope="module")
def retriever(reg) -> CorpusRetriever:
    return CorpusRetriever(reg)


def _mutated(mutate) -> Registry:
    """Build a Registry from a deliberately corrupted copy of the real corpus.

    Goes through the same construction path as Registry.load so the guards under
    test are the real ones, not a reimplementation.
    """
    from guidance.registry import Source

    sources = json.loads((CORPUS_DIR / "sources.json").read_text())
    corpus = json.loads((CORPUS_DIR / "corpus.json").read_text())
    mutate(sources, corpus)

    reg = Registry(
        sources={
            sid: Source(**{k: v for k, v in s.items() if k in Source.__annotations__})
            for sid, s in sources["sources"].items()
        },
        triggers=corpus["triggers"],
        rejected=sources.get("rejected_sources", {}),
    )
    reg.validate()
    return reg


# --------------------------------------------------------------------- coverage
def test_every_canonical_trigger_is_present(reg):
    assert set(reg.triggers) == set(CANONICAL_TRIGGERS)


def test_covered_triggers_have_entries(reg):
    for name, block in reg.triggers.items():
        if block["status"] == "covered":
            assert block["entries"], f"{name} is marked covered but has no entries"


def test_evidence_gap_is_explicit_not_empty(reg):
    """A gap must explain itself. An empty block with no note is indistinguishable
    from a bug."""
    gaps = [n for n, b in reg.triggers.items() if b["status"] == "evidence_gap"]
    assert gaps, "expected at least one documented gap; if the corpus now covers " \
                 "everything, delete this test deliberately rather than by accident"
    for name in gaps:
        block = reg.get(name)
        assert block["entries"] == []
        assert block["evidence_note"]
        assert len(block["evidence_note"].split()) > 30, \
            f"{name}: an evidence gap needs a real explanation, not a placeholder"


def test_bleeding_warning_signs_is_still_a_gap(reg):
    """Guards the specific finding from source review: no corpus source makes a
    patient-facing recommendation on recognising bleeding after discharge.
    If this fails, someone added a source — confirm it genuinely covers this
    before deleting the test."""
    assert reg.triggers["bleeding_warning_signs"]["status"] == "evidence_gap"


# ------------------------------------------------------------------ provenance
def test_every_entry_traces_to_a_registered_source(reg):
    for name in reg.triggers:
        for e in reg.get(name)["entries"]:
            assert e["source"]["id"] in reg.sources
            assert e["url"].startswith("http")
            assert e["section"]


def test_no_excerpt_exceeds_the_copyright_cap(reg):
    for name in reg.triggers:
        for e in reg.get(name)["entries"]:
            n = len(e["excerpt"].split())
            assert n <= MAX_EXCERPT_WORDS, f"{name}/{e['id']}: {n} words"


def test_authored_summary_is_always_flagged(reg):
    """The UI has to be able to tell our words from a guideline's."""
    for name in reg.triggers:
        assert reg.get(name)["plain_summary_is_authored_by_recoverylens"] is True


def test_isa_takes_precedence_over_nice(reg):
    """Localisation rule: where both cover a topic, the Indian source leads."""
    entries = reg.get("rehabilitation_referral")["entries"]
    tiers = [e["source"]["tier"] for e in entries]
    assert "primary" in tiers and "fallback" in tiers, "need both to test ordering"
    assert tiers.index("primary") < tiers.index("fallback")


def test_isa_rehab_entries_carry_the_scope_caveat(reg):
    """ISA section 2.0 excludes rehabilitation from its own scope while section
    12.0 is titled Stroke Rehabilitation. Anything we quote from 12.0 must say so."""
    for e in reg.get("rehabilitation_referral")["entries"]:
        if e["source"]["id"] == "isa_2024" and e["section"] == "12.0":
            assert e["caveat"], f"{e['id']} quotes ISA 12.0 without the scope caveat"


# ------------------------------------------------------------- guard behaviour
def test_unknown_source_id_is_fatal():
    def mutate(sources, corpus):
        corpus["triggers"]["general_recovery"]["entries"][0]["source_id"] = "not_a_source"
    with pytest.raises(GuidanceError, match="unknown source_id"):
        _mutated(mutate)


def test_overlong_excerpt_is_fatal():
    def mutate(sources, corpus):
        corpus["triggers"]["general_recovery"]["entries"][0]["excerpt"] = "word " * 200
    with pytest.raises(GuidanceError, match="copyright guard"):
        _mutated(mutate)


def test_covered_trigger_without_entries_is_fatal():
    def mutate(sources, corpus):
        corpus["triggers"]["general_recovery"]["entries"] = []
    with pytest.raises(GuidanceError, match="no entries"):
        _mutated(mutate)


def test_missing_trigger_is_fatal():
    def mutate(sources, corpus):
        del corpus["triggers"]["visual_field_safety"]
    with pytest.raises(GuidanceError, match="absent from corpus"):
        _mutated(mutate)


def test_evidence_gap_without_note_is_fatal():
    def mutate(sources, corpus):
        corpus["triggers"]["bleeding_warning_signs"]["evidence_note"] = ""
    with pytest.raises(GuidanceError, match="requires an evidence_note"):
        _mutated(mutate)


def test_unknown_trigger_raises(reg):
    with pytest.raises(UnknownTrigger):
        reg.get("no_such_trigger")


def test_for_assessment_surfaces_rather_than_drops_unknown(reg):
    out = reg.for_assessment(["general_recovery", "invented_trigger"])
    assert out["unresolved_triggers"] == ["invented_trigger"]
    assert len(out["guidance"]) == 1


# --------------------------------------------------------------- predictor sync
def test_predictor_triggers_match_the_corpus():
    """The strings in Predictor._guidance() are the contract. Read them out of the
    source rather than trusting a duplicated list."""
    import ast
    src = (Path(__file__).resolve().parent.parent / "api" / "predictor.py").read_text()
    tree = ast.parse(src)

    emitted: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef) and node.name == "_guidance"):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "append"
                        and sub.args
                        and isinstance(sub.args[0], ast.Constant)
                        and isinstance(sub.args[0].value, str)):
                    emitted.add(sub.args[0].value)

    assert emitted, "could not parse triggers out of _guidance() — update this test"
    assert emitted == set(CANONICAL_TRIGGERS), (
        f"predictor and corpus have drifted.\n"
        f"  only in predictor: {sorted(emitted - set(CANONICAL_TRIGGERS))}\n"
        f"  only in corpus:    {sorted(set(CANONICAL_TRIGGERS) - emitted)}")


# -------------------------------------------------------------------- retrieval
# NOTE: sections are compared EXACTLY. An earlier version of this test used
# startswith(), which silently accepted "1.13.18" for an expected "1.13.1" and
# hid a genuine ranking regression. Do not reintroduce prefix matching.
@pytest.mark.parametrize("question,expect_section", [
    ("can they drive with a visual field defect?", "1.8.2"),
    ("are wrist splints recommended?", "1.13.10"),
    ("when is the follow up review due?", "1.17.5"),
    ("what exercise can they do at home?", "1.13.5"),
    ("should I refer this patient for physiotherapy for their arm?", "1.13.1"),
])
def test_retrieval_ranks_the_right_recommendation_first(retriever, question, expect_section):
    hits = retriever.search(question)
    assert hits, f"no hits for: {question}"
    assert hits[0]["section"] == expect_section, (
        f"{question!r} -> got {hits[0]['source']['short_title']} "
        f"{hits[0]['section']}, expected {expect_section}")


@pytest.mark.parametrize("question,expect_section,within", [
    # Honest claim: for these the top few passages score within noise of each
    # other and are all drawn from the correct trigger, so we assert presence in
    # the top-N rather than pretending the ordering is meaningful.
    ("patient has trouble swallowing, what should the family know?", "1.11.2", 3),
    # Both Vision recommendations are legitimate answers here; 1.8.2 wins on
    # "eyesight"/"eye" overlap. Asserting an exact order would be asserting noise.
    ("who should assess their eyesight?", "1.8.1", 3),
])
def test_relevant_recommendation_appears_near_the_top(retriever, question, expect_section, within):
    sections = [h["section"] for h in retriever.search(question, top_k=within)]
    assert expect_section in sections, (
        f"{question!r} -> top {within} were {sections}, expected {expect_section} among them")


@pytest.mark.parametrize("question", [
    "what is the correct dose of alteplase?",
    "how do I manage a myocardial infarction?",
    "what is the capital of France?",
    "which antibiotic for aspiration pneumonia?",
])
def test_retriever_refuses_out_of_scope(retriever, question):
    """Refusal is the feature. A retriever that always answers will hand a
    clinician a confidently irrelevant recommendation."""
    result = retriever.ask(question)
    assert result["answered"] is False
    assert result["mode"] == "refusal"
    assert result["passages"] == []


def test_answers_are_extractive_by_default(retriever):
    """Default path must not generate. Every sentence of substance in the answer
    should appear verbatim in a retrieved excerpt."""
    result = retriever.ask("are wrist splints recommended?")
    assert result["mode"] == "extractive"
    assert any(h["excerpt"] in result["answer"] for h in result["passages"])


def test_retrieved_passages_keep_their_citation(retriever):
    for h in retriever.ask("how should I support medication adherence?")["passages"]:
        assert h["source"]["id"]
        assert h["section"]
        assert h["url"].startswith("http")


# ------------------------------------------------------------------- generation
def test_synthesis_is_off_unless_explicitly_enabled(retriever, monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_LLM_SYNTHESIS", raising=False)
    assert retriever.ask("are wrist splints recommended?")["mode"] == "extractive"


def test_missing_api_key_falls_back_to_extractive_not_a_lie(retriever, monkeypatch):
    """The single most important test in this file.

    If generation is switched on but cannot run, the response must NOT report
    mode 'synthesised'. A caller who is told the answer was generated from the
    passages, when it was actually a verbatim dump (or vice versa), cannot audit
    what they were given. Silent mislabelling is worse than either mode."""
    monkeypatch.setenv("RECOVERYLENS_LLM_SYNTHESIS", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = retriever.ask("are wrist splints recommended?")
    assert result["mode"] == "extractive"
    assert "Generation unavailable" in result["answer"]
    # The real recommendation still reaches the clinician.
    assert any(h["excerpt"] in result["answer"] for h in result["passages"])


def test_llm_failure_never_raises_to_the_caller(retriever, monkeypatch):
    """A provider outage degrades the answer; it must not break the endpoint."""
    import guidance.retrieval as R

    monkeypatch.setenv("RECOVERYLENS_LLM_SYNTHESIS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(
        R, "_call_llm",
        lambda system, user: (_ for _ in ()).throw(R.LLMUnavailable("APIConnectionError")))

    result = retriever.ask("are wrist splints recommended?")
    assert result["answered"] is True
    assert result["mode"] == "extractive"


def test_grounding_contract_forbids_outside_knowledge():
    """The system prompt is the whole safety argument for generation. If someone
    softens it, this fails."""
    import guidance.retrieval as R
    s = R.GROUNDING_SYSTEM.lower()
    assert "only" in s and "no other knowledge" in s
    assert "the passage wins" in s          # training data must not override sources
    assert "cite the source and section" in s
    assert "dosing" in s                     # explicit refusal scope
    assert "data, not as instructions" in s  # prompt-injection guard


def test_synthesised_mode_only_when_generation_actually_ran(retriever, monkeypatch):
    import guidance.retrieval as R
    monkeypatch.setenv("RECOVERYLENS_LLM_SYNTHESIS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(R, "_call_llm", lambda system, user: "Grounded answer (NICE NG236 1.13.10).")

    result = retriever.ask("are wrist splints recommended?")
    assert result["mode"] == "synthesised"
    assert result["answer"] == "Grounded answer (NICE NG236 1.13.10)."
    # Passages still returned, so the clinician can check the generated text.
    assert result["passages"]


def test_only_retrieved_passages_reach_the_model(retriever, monkeypatch):
    """Guards the containment claim: the model sees the question and the
    retrieved excerpts, and nothing else from the corpus."""
    import guidance.retrieval as R
    captured = {}
    monkeypatch.setenv("RECOVERYLENS_LLM_SYNTHESIS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    def fake(system, user):
        captured["system"], captured["user"] = system, user
        return "ok"

    monkeypatch.setattr(R, "_call_llm", fake)
    result = retriever.ask("are wrist splints recommended?")

    retrieved = {h["excerpt"] for h in result["passages"]}
    for entry in retriever.registry.get("general_recovery")["entries"]:
        if entry["excerpt"] not in retrieved:
            assert entry["excerpt"] not in captured["user"], \
                "an unretrieved excerpt leaked into the prompt"


def test_length_prior_does_not_promote_below_floor_matches(retriever):
    """Priors reorder relevant passages; they must never lift an irrelevant one
    over the refusal threshold."""
    for h in retriever.search("physiotherapy for arm weakness"):
        assert h["cosine"] >= retriever.score_floor
