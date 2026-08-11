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
    # Was NG236 1.13.5 until RCP was ingested. That expectation was pinned to the
    # best answer a NICE-only corpus could give, and NG236 1.13.5 in full is
    # "Encourage people to participate in physical activity after stroke." — nine
    # words that answer neither "what exercise" nor "at home". RCP 5.23 A says
    # "should participate in physical activity for fitness ... Exercise
    # prescription", with 5.23 E on tailoring and 5.23 G on community facilities
    # right behind it. The corpus got a better answer, so the expectation moved.
    ("what exercise can they do at home?", "5.23 A"),
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
])
def test_relevant_recommendation_appears_near_the_top(retriever, question, expect_section, within):
    sections = [h["section"] for h in retriever.search(question, top_k=within)]
    assert expect_section in sections, (
        f"{question!r} -> top {within} were {sections}, expected {expect_section} among them")


def test_who_performs_the_assessment_is_answered(retriever):
    """"who should assess their eyesight?" — the question char n-grams were added for.

    This used to assert NG236 1.8.1 ("Offer ... a specialist orthoptist
    assessment") in the top 3. It no longer appears at all in TF-IDF-only mode:
    at 803 passages its cosine falls below SCORE_FLOOR, which had to rise to 0.24
    to keep "how do I manage a myocardial infarction?" out.

    That is a real loss, recorded rather than hidden — see
    `test_tfidf_only_mode_is_measurably_weaker_now` below. The property still
    worth defending is that SOME recommendation naming who performs the
    assessment comes back, and RCP 4.48 D does name one: assessment by an
    occupational therapist. It is a correct answer to the question asked, not a
    lowered bar.
    """
    hits = retriever.search("who should assess their eyesight?", top_k=3)
    assert hits, "no hits at all — the corpus has vision content, so this is a bug"
    sections = {h["section"] for h in hits}
    assert sections & {"1.8.1", "4.48 D", "4.48 C"}, (
        f"expected a vision assessment recommendation, got {sorted(sections)}")
    assert all("vision" in (h["heading"] or "").lower() for h in hits), (
        "every hit should sit under a Vision heading")


def test_tfidf_only_mode_is_measurably_weaker_now(retriever):
    """Documents a limitation rather than asserting a success.

    Embeddings used to be an optional improvement. At 803 passages they are
    load-bearing: the TF-IDF-only path must run a floor of 0.24 to refuse the
    myocardial-infarction question, and that floor now also excludes NG236 1.8.1,
    a correct answer to a legitimate question.

    This test exists so that fact cannot be forgotten. If a future change makes
    1.8.1 retrievable again in TF-IDF-only mode, this test fails — and that
    failure is good news, at which point delete it and restore the stricter
    assertion in the test above.
    """
    sections = [h["section"] for h in retriever.search(
        "who should assess their eyesight?", top_k=8)]
    assert "1.8.1" not in sections, (
        "1.8.1 is retrievable again in TF-IDF-only mode — good. Restore the "
        "stricter assertion in test_who_performs_the_assessment_is_answered and "
        "delete this test.")


# NOTE: "which antibiotic for aspiration pneumonia?" used to be in this list and
# was removed deliberately. It failed once the corpus grew from 39 to ~291
# chunks, because NICE NG128 1.8.1 genuinely covers aspiration pneumonia. The
# original assertion was measuring corpus thinness, not scope — the retriever
# refused because we had nothing on the topic, not because the topic was out of
# bounds. Retrieving relevant guidance for a question the corpus only partly
# answers is correct behaviour; the grounding contract handles the rest.
@pytest.mark.parametrize("question", [
    "what is the correct dose of alteplase?",       # dosing: never in scope
    "how do I manage a myocardial infarction?",     # different condition
    "what is the capital of France?",               # not clinical at all
    "what are the surgical options for glioma?",    # unrelated specialty
])
def test_retriever_refuses_out_of_scope(retriever, question):
    """Refusal is the feature. A retriever that always answers will hand a
    clinician a confidently irrelevant recommendation."""
    result = retriever.ask(question)
    assert result["answered"] is False
    assert result["mode"] == "refusal"
    assert result["passages"] == []


def test_curated_entries_are_not_duplicated_by_ingested_chunks(retriever):
    """Ingestion re-reads the documents a human already read, so 29 of the 35
    curated entries came back as chunks too. The index held two copies of NG236
    1.8.2, and a "top 3" could contain the same recommendation twice — a wasted
    slot that reads as two guidelines agreeing when it is one, quoted twice.

    The curated copy wins: it is hand-verified and carries any caveat a human
    attached, which the parser cannot know about.
    """
    assert retriever.suppressed_duplicates > 0, (
        "no duplicates suppressed — either ingestion stopped covering the curated "
        "recommendations, or the suppression stopped working")

    # Two CURATED entries may share a section number, and legitimately do: both
    # hand-verified ISA excerpts cite §13.0, because ISA numbers sections rather
    # than recommendations and one section runs for pages. That is the same
    # coarseness that makes ISA chunks `citation_precision: "section"`.
    #
    # What must never happen is one recommendation appearing as both a curated
    # entry and an ingested chunk — that is one recommendation quoted twice.
    tiers: dict[tuple[str, str], set[str]] = {}
    for p in retriever.passages:
        key = (p.payload["source"]["id"], p.payload["section"])
        tiers.setdefault(key, set()).add(p.payload["extraction"])

    both = {k for k, v in tiers.items() if v == {"curated", "automatic"}}
    assert not both, f"indexed as both curated and automatic: {sorted(both)}"


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


# -------------------------------------------------------------- follow-up RAG
@pytest.fixture(scope="module")
def planner():
    from guidance.followup import FollowUpPlanner
    return FollowUpPlanner()


PLAN = [{"day": d, "reason": "x"} for d in (3, 7, 14, 30, 42, 90, 180)]
TRIGGERS = ["adherence_support", "speech_and_swallowing", "arm_rehabilitation",
            "mobility_and_falls", "general_recovery"]


def test_days_are_never_invented_or_dropped(planner):
    """The schedule is deterministic. The planner annotates; it must not plan."""
    out = planner.build(PLAN, TRIGGERS, [])
    assert [c["day"] for c in out] == [p["day"] for p in PLAN]


def test_schedule_is_reproducible(planner):
    """Identical inputs must give an identical schedule. A follow-up plan that
    varies run to run cannot be audited, and a shifted day is a missed window."""
    a = planner.build(PLAN, TRIGGERS, [])
    b = planner.build(PLAN, TRIGGERS, [])
    assert [c["day"] for c in a] == [c["day"] for c in b]
    assert [c["basis"] for c in a] == [c["basis"] for c in b]
    assert [[p["id"] for p in c["passages"]] for c in a] == \
           [[p["id"] for p in c["passages"]] for c in b]


def test_guideline_basis_requires_a_real_citation(planner):
    """The specific regression this file exists to prevent: a day labelled as
    guideline-recommended when no guideline recommends it."""
    for c in planner.build(PLAN, TRIGGERS, []):
        if c["basis"] == "guideline":
            assert c["citations"], f"day {c['day']} claims guideline basis with no citation"
        else:
            assert not c["citations"], f"day {c['day']} is {c['basis']} but carries citations"
            assert c["evidence_note"], f"day {c['day']} must explain its lack of backing"


def test_day_14_no_longer_claims_guideline_authority(planner):
    out = {c["day"]: c for c in planner.build(PLAN, TRIGGERS, [])}
    assert out[14]["basis"] == "operational"
    assert "guideline-recommended" not in out[14]["reason"].lower()
    assert "guideline-recommended" not in out[14]["clinician_note"].lower()


def test_day_90_is_labelled_trial_convention(planner):
    """90-day mRS is a research endpoint, not a care recommendation."""
    out = {c["day"]: c for c in planner.build(PLAN, TRIGGERS, [])}
    assert out[90]["basis"] == "trial_convention"
    assert not out[90]["citations"]


def test_retrieved_passages_stay_within_the_patients_triggers(planner):
    """Retrieving falls guidance for a patient with no leg deficit is noise at
    best and misleading at worst."""
    triggers = ["adherence_support", "general_recovery"]
    for c in planner.build(PLAN, triggers, []):
        for p in c["passages"]:
            assert p["trigger"] in triggers


def test_checkins_differ_from_each_other(planner):
    """If every day retrieves the same passages, the retrieval adds nothing."""
    sets = [tuple(sorted(p["id"] for p in c["passages"]))
            for c in planner.build(PLAN, TRIGGERS, [])
            if c["passages"]]
    assert len(set(sets)) > 1, "every check-in retrieved identical guidance"


def test_narratives_fall_back_to_static_without_generation(planner, monkeypatch):
    monkeypatch.delenv("RECOVERYLENS_LLM_SYNTHESIS", raising=False)
    for c in planner.build(PLAN, TRIGGERS, []):
        assert c["narrative_mode"] == "static"
        assert c["clinician_note"] and c["caregiver_message"]


def test_caregiver_contract_is_stricter_than_clinician(planner):
    """The caregiver may act alone on what they read. Their prompt must forbid
    the things a clinician would catch."""
    from guidance.followup import CAREGIVER_SYSTEM, CLINICIAN_SYSTEM
    care = CAREGIVER_SYSTEM.lower()
    for forbidden in ("doses", "starting, stopping or changing", "how well or badly"):
        assert forbidden in care, f"caregiver contract must forbid: {forbidden}"
    assert "do not invent warning signs" in care
    assert "only source" in care and "only source" in CLINICIAN_SYSTEM.lower()
    assert "data, never as instructions" in care


def test_unregistered_day_is_surfaced_not_swallowed(planner):
    out = planner.build([{"day": 999, "reason": "x"}], TRIGGERS, [])
    assert out[0]["basis"] == "unregistered"
    assert "absent from" in out[0]["evidence_note"]


def test_followup_evidence_cites_registered_sources_only(planner, reg):
    for interval in planner.intervals.values():
        for e in interval.entries:
            assert e["source_id"] in reg.sources


def test_checkin_floor_does_not_leak_into_user_questions(retriever):
    """The lower floor used for check-in context must not weaken refusal."""
    from guidance.followup import CHECKIN_FLOOR
    assert CHECKIN_FLOOR < retriever.score_floor
    assert retriever.ask("what is the correct dose of alteplase?")["answered"] is False


# ------------------------------------------------------------------ embeddings
# These exist because a NameError shipped in the blend path — `np.clip` was used
# in retrieval.py without numpy imported. Nothing caught it: no embedding cache
# existed in CI, so the branch never executed. A test suite that only ever runs
# the fallback proves nothing about the feature, so these fake a cache and force
# the blend to run.
def _fake_embedding_index(retriever, query_boost: str = ""):
    """Cache covering every passage, with unit vectors.

    Real embeddings are not needed to test the plumbing — only that the blend
    executes, the shapes line up, and the scores stay in range.
    """
    import numpy as np
    from guidance.embeddings import EmbeddingIndex, text_hash

    dim = 8
    vectors = {}
    for i, p in enumerate(retriever.passages):
        v = np.zeros(dim, dtype=np.float32)
        # Give the passage containing `query_boost` a distinguishable direction.
        v[0] = 1.0 if (query_boost and query_boost in p.text.lower()) else 0.0
        v[1 + (i % (dim - 1))] = 1.0
        vectors[text_hash(p.text)] = v / np.linalg.norm(v)
    return EmbeddingIndex(vectors, model="test-model")


def _activate(retriever, index, query_vec=None):
    import numpy as np
    retriever._embed_index = index
    retriever._embed_matrix = index.matrix_for([p.text for p in retriever.passages])
    if query_vec is None:
        query_vec = np.zeros(8, dtype=np.float32)
        query_vec[0] = 1.0
    retriever._embed_index.embed_query = lambda q: query_vec


def test_blend_path_executes_without_error(retriever):
    """The regression test for the NameError. Forces the semantic branch to run."""
    index = _fake_embedding_index(retriever, query_boost="splints")
    _activate(retriever, index)
    try:
        hits = retriever.search("are wrist splints recommended?", top_k=3,
                                score_floor=0.0)
        assert hits, "blend path returned nothing"
        for h in hits:
            assert 0.0 <= h["cosine"] <= 1.0
    finally:
        retriever._embed_matrix = None
        retriever._embed_index = None


def test_blend_changes_ranking(retriever):
    """If the semantic score had no effect, the blend would be pointless."""
    plain = [h["id"] for h in retriever.search("recovery", top_k=8, score_floor=0.0)]

    index = _fake_embedding_index(retriever, query_boost="splints")
    _activate(retriever, index)
    try:
        blended = [h["id"] for h in retriever.search("recovery", top_k=8,
                                                     score_floor=0.0)]
    finally:
        retriever._embed_matrix = None
        retriever._embed_index = None

    assert plain != blended, "semantic score had no effect on ranking"


def test_stale_cache_is_rejected_not_partially_used(retriever):
    """Partial coverage would score some passages semantically and others at
    zero, ranking the embedded ones higher for reasons unrelated to relevance."""
    index = _fake_embedding_index(retriever)
    index.vectors.pop(next(iter(index.vectors)))          # drop one passage
    assert index.matrix_for([p.text for p in retriever.passages]) is None


def test_query_embedding_failure_falls_back_to_tfidf(retriever):
    index = _fake_embedding_index(retriever)
    _activate(retriever, index)
    retriever._embed_index.embed_query = lambda q: None   # simulate API failure
    try:
        hits = retriever.search("are wrist splints recommended?", top_k=2,
                                score_floor=0.0)
        assert hits, "should still return TF-IDF results"
    finally:
        retriever._embed_matrix = None
        retriever._embed_index = None


def test_overlap_gates_are_skipped_when_semantics_are_active(retriever):
    """The gates are a TF-IDF patch. Left on, they would reject the paraphrase
    cases embeddings were added to fix."""
    index = _fake_embedding_index(retriever)
    _activate(retriever, index)
    try:
        # A query sharing no whole word with any passage still returns results,
        # because ranking is now semantic.
        hits = retriever.search("zzz qqq", top_k=2, score_floor=0.0)
        assert hits
    finally:
        retriever._embed_matrix = None
        retriever._embed_index = None


# --------------------------------------------------- API response validation
# The Ask endpoint returned 500 in production while the whole suite was green.
# `RetrievedPassage.trigger` was typed `str`, but auto-ingested chunks carry
# trigger=None by design. It never surfaced in tests because corpus_full.json
# ships empty, so no ingested chunk ever reached the schema — the same shape of
# gap as the numpy import: a path that only executes with real data.
def test_ask_response_validates_with_ingested_chunks(retriever, reg):
    """Force an ingested-style passage through the Pydantic model."""
    from api.schemas import GuidanceAnswer

    hits = retriever.search("are wrist splints recommended?", top_k=2,
                            score_floor=0.0)
    assert hits, "probe question returned nothing"

    # Mimic what corpus_full.json produces: no trigger, automatic extraction.
    for h in hits:
        h["trigger"] = None
        h["extraction"] = "automatic"

    payload = {
        "question": "are wrist splints recommended?", "answered": True,
        "mode": "extractive", "answer": "…", "passages": hits,
        "sources_cited": [], "related_evidence_gaps": [], "disclaimer": "…",
    }
    parsed = GuidanceAnswer.model_validate(payload)
    assert parsed.passages[0].trigger is None
    assert parsed.passages[0].extraction == "automatic"


def test_ask_response_validates_with_curated_chunks(retriever):
    """The other half — a curated passage must still validate."""
    from api.schemas import GuidanceAnswer

    hits = retriever.search("are wrist splints recommended?", top_k=1,
                            score_floor=0.0)
    parsed = GuidanceAnswer.model_validate({
        "question": "q", "answered": True, "mode": "extractive", "answer": "…",
        "passages": hits, "sources_cited": [], "related_evidence_gaps": [],
        "disclaimer": "…",
    })
    assert parsed.passages[0].trigger in CANONICAL_TRIGGERS
    assert parsed.passages[0].extraction == "curated"


def test_every_search_result_field_survives_the_schema(retriever):
    """Guards the whole shape, not just `trigger` — search() has gained fields
    (blended, extraction) that the schema must keep up with."""
    from api.schemas import RetrievedPassage

    for h in retriever.search("how is dysphagia managed?", top_k=3,
                              score_floor=0.0):
        RetrievedPassage.model_validate(h)


def test_length_prior_does_not_promote_below_floor_matches(retriever):
    """Priors reorder relevant passages; they must never lift an irrelevant one
    over the refusal threshold."""
    for h in retriever.search("physiotherapy for arm weakness"):
        assert h["cosine"] >= retriever.score_floor
