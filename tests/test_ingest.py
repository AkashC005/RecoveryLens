"""
Tests for the guideline ingestion parsers.

Fixtures are real text from the real documents — `tests/fixtures/rcp_section.txt`
and `isa_section.txt` — not invented examples. A parser tested only on text
shaped the way the parser expects proves nothing.

The two properties worth defending
----------------------------------
1. NUMBERS ARE NEVER ALTERED. The footnote stripper for the ISA PDF turned
   "within 4.5 hours of symptom onset" into "within 4. hours" in its first
   version. A silently changed drug window, quoted verbatim with a citation
   attached, is the worst thing this module could produce.
2. SECTION NUMBERS ARE NEVER INVENTED. A chunk whose number cannot be read from
   the document is dropped. That is what makes an auto-extracted chunk citable.
"""

from pathlib import Path

import pytest

from guidance.ingest import (ISA_SECTION_CAVEAT, MAX_WORDS, RCP_MAX_WORDS,
                             _strip_refs, parse_isa, parse_rcp)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def rcp():
    return parse_rcp((FIXTURES / "rcp_section.txt").read_text(encoding="utf-8"),
                     "ncgs_2023")


@pytest.fixture(scope="module")
def isa():
    return parse_isa((FIXTURES / "isa_section.txt").read_text(encoding="utf-8"),
                     "isa_2024")


# ------------------------------------------------------- the number-safety rule
@pytest.mark.parametrize("text, must_keep", [
    ("IV rtPA administered to eligible patients within 4.5 hours of onset", "4.5"),
    ("maintained below 180/105 mmHg for at least the first 24 hours", "180/105"),
    ("blood pressure carefully lowered so that systolic is <185 mmHg", "185"),
    ("clopidogrel 75 mg daily as the standard treatment", "75"),
    ("oxygen saturation >94% should be maintained", "94"),
    ("Hypoglycemia (blood glucose <60 mg/dL) should be treated", "60"),
    ("warfarin (target INR 2.5, range 2.0 to 3.0)", "2.5"),
])
def test_footnote_stripping_never_damages_a_quantity(text, must_keep):
    """The regression that motivated the guard clause in `_strip_refs`."""
    assert must_keep in _strip_refs(text)


@pytest.mark.parametrize("text, gone, kept", [
    ("rehabilitation is a major contributor for stroke survivors.27 The effects",
     "27", "survivors. The effects"),
    ("the effect of task-oriented exercise training was evident.180",
     "180", "was evident."),
    ("secondary prevention measures include, but are not limited to49",
     "49", "are not limited to"),
    ("risk factor analysis in Kolkata demonstrated hypertension.181,182",
     "181", "demonstrated hypertension."),
])
def test_footnote_markers_are_removed(text, gone, kept):
    out = _strip_refs(text)
    assert gone not in out
    assert kept in out


def test_stripper_abandons_the_attempt_rather_than_corrupt():
    """The invariant, stated directly.

    If stripping would remove a decimal or ratio quantity, the original text is
    returned untouched. A visible "survivors.27" is cosmetic; "4. hours" is not.
    """
    text = "give within 4.5 hours as shown in the trial.27"
    out = _strip_refs(text)
    assert "4.5" in out


# ------------------------------------------------------------------------- RCP
def test_rcp_cites_section_plus_the_guidelines_own_letter(rcp):
    sections = [c.section for c in rcp]
    assert "5.7 A" in sections
    assert "5.7 B" in sections
    assert "5.6 A" in sections
    # Three-level section numbers exist in chapter 4 and must survive.
    assert "4.23.1 A" in sections


def test_rcp_ignores_background_prose(rcp):
    """Only text after the "Recommendations" marker is citable.

    The prose before it discusses the evidence. Quoting discussion as a
    recommendation would misrepresent the source in the way that matters most.
    """
    joined = " ".join(c.text for c in rcp)
    assert "usually restricted to long-term secondary prevention" not in joined
    assert "reduces the risk of recurrent stroke and other vascular events" not in joined


def test_rcp_drops_the_truncated_preview(rcp):
    """The site renders a "…Show more" preview followed by the full text."""
    assert not any("Show more" in c.text for c in rcp)


def test_rcp_keeps_conditions_attached_to_their_recommendation(rcp):
    """5.7 A carries its conditions as sub-bullets, and one of them is
    "should not be given if brain imaging has identified significant
    haemorrhage". Dropping it to fit a word budget would invert the advice."""
    a = next(c for c in rcp if c.section == "5.7 A")
    assert "should not be given if brain imaging" in a.text
    assert "severe hypertension" in a.text
    assert len(a.text.split()) <= RCP_MAX_WORDS


def test_rcp_captures_year_tags(rcp):
    by_section = {c.section: c for c in rcp}
    assert by_section["5.7 A"].year_tag == "2023"
    assert by_section["4.23.1 A"].year_tag == "2016"


def test_rcp_records_the_section_heading(rcp):
    by_section = {c.section: c for c in rcp}
    assert by_section["5.7 A"].heading == "Anticoagulation"
    assert by_section["5.6 B"].heading == "Antiplatelet treatment"


def test_rcp_is_recommendation_precision(rcp):
    assert all(c.citation_precision == "recommendation" for c in rcp)
    assert all(c.caveat is None for c in rcp)


def test_rcp_ids_are_unique(rcp):
    ids = [c.id for c in rcp]
    assert len(ids) == len(set(ids))


def test_rcp_marks_everything_automatic(rcp):
    """Nothing auto-extracted may pass itself off as hand-verified."""
    assert all(c.extraction == "automatic" for c in rcp)


# ------------------------------------------------------------------------- ISA
def test_isa_can_only_cite_a_section(isa):
    """ISA numbers sections but not recommendations, so this is the finest
    citation the document supports. Recorded, not hidden."""
    assert isa, "fixture produced no chunks"
    assert all(c.citation_precision == "section" for c in isa)
    assert all(c.section.endswith(".0") for c in isa)


def test_isa_carries_the_coarseness_caveat_on_every_chunk(isa):
    """A section number LOOKS like a precise citation. The caveat is what stops
    a reader treating it as one."""
    assert all(c.caveat == ISA_SECTION_CAVEAT for c in isa)


def test_isa_ids_are_unique_without_inventing_numbering(isa):
    """Several chunks share one section, so ids need an ordinal — but the
    ordinal must never leak into `section`. "12.0 (3)" would imply ISA has a
    numbering it does not have."""
    assert len(set(c.id for c in isa)) == len(isa)
    assert not any("(" in c.section or " " in c.section for c in isa)


def test_isa_reaches_the_rehabilitation_section(isa):
    """§12.0 is the reason ISA is ingested at all — it is the only
    post-discharge material in the document."""
    rehab = [c for c in isa if c.section == "12.0"]
    assert rehab
    assert any("specialist rehabilitation team" in c.text for c in rehab)


def test_isa_preserves_the_thrombolysis_window(isa):
    """The specific text that the first footnote stripper corrupted."""
    joined = " ".join(c.text for c in isa)
    assert "4.5 hours" in joined
    assert "4. hours" not in joined


def test_isa_groups_bullets_rather_than_emitting_fragments(isa):
    """A single ISA bullet is often a sentence fragment that only means
    something alongside its neighbours."""
    assert all(len(c.text.split()) >= 8 for c in isa)
    assert all(len(c.text.split()) <= MAX_WORDS for c in isa)


def test_isa_ignores_narrative_before_the_recommendations_marker(isa):
    joined = " ".join(c.text for c in isa)
    assert "Evidence describes rehabilitation as a major contributor" not in joined


# ---------------------------------------------------- unparseable input is safe
def test_parsers_return_nothing_rather_than_guess():
    """A page whose structure has changed must yield zero chunks, not chunks
    with invented section numbers. `main()` treats zero as a failure and leaves
    the corpus alone."""
    junk = "Some prose with no numbering at all.\n\nMore prose.\n"
    assert parse_rcp(junk, "ncgs_2023") == []
    assert parse_isa(junk, "isa_2024") == []


def test_rcp_letters_outside_a_recommendations_block_are_not_chunks():
    """A stray capital letter on its own line must not become a citation."""
    text = "### 5.1 Something\n\nA\n\nThis is background prose, not a recommendation.\n"
    assert parse_rcp(text, "ncgs_2023") == []


# ------------------------------------------------- the one-file safety boundary
def test_curated_and_automatic_live_in_one_file_separated_by_extraction():
    """corpus.json holds both tiers. The `extraction` field is the boundary.

    It used to be enforced by ingested chunks having no `trigger`, which was a
    guarantee living in a missing value — and it broke once something needed
    trigger to be non-null.
    """
    import json
    from guidance.registry import HERE

    data = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))
    assert "triggers" in data and "chunks" in data
    assert not (HERE / "corpus_full.json").exists(), \
        "the second corpus file should be gone — one file was the point"

    for name, block in data["triggers"].items():
        for entry in block.get("entries", []):
            assert entry.get("extraction", "curated") == "curated", \
                f"{name}/{entry.get('id')} is auto-extracted but sits in a " \
                f"patient-facing block"

    for chunk in data["chunks"]:
        assert chunk["extraction"] == "automatic"


def test_registry_rejects_automatic_material_in_a_patient_facing_block():
    """The check that makes the boundary real rather than documented."""
    import copy

    from guidance.registry import GuidanceError, registry

    reg = copy.deepcopy(registry)
    covered = next(t for t, b in reg.triggers.items() if b["status"] == "covered")
    reg.triggers[covered]["entries"][0]["extraction"] = "automatic"

    with pytest.raises(GuidanceError, match="hand-verified"):
        reg.validate()


def test_registry_rejects_section_precision_in_a_patient_facing_block():
    import copy

    from guidance.registry import GuidanceError, registry

    reg = copy.deepcopy(registry)
    covered = next(t for t, b in reg.triggers.items() if b["status"] == "covered")
    reg.triggers[covered]["entries"][0]["citation_precision"] = "section"

    with pytest.raises(GuidanceError, match="citation_precision"):
        reg.validate()


# -------------------------------------------------------- coarse-citation cap
def _passage(pid, precision, words=20):
    from guidance.retrieval import Passage

    return Passage(entry_id=pid, trigger=None, text="x " * words,
                   payload={"id": pid, "excerpt": "word " * words,
                            "citation_precision": precision})


def test_coarse_citations_cannot_fill_an_entire_answer():
    """A question well matched by ISA's rehabilitation section would otherwise
    return four passages all citing §12.0 — an answer built entirely on
    references the reader cannot check precisely."""
    from guidance.retrieval import _cap_coarse_citations

    scored = [(1.0 - i / 10, 1.0 - i / 10, _passage(f"isa{i}", "section"))
              for i in range(4)]
    scored += [(0.5 - i / 100, 0.5, _passage(f"rcp{i}", "recommendation"))
               for i in range(4)]

    out = _cap_coarse_citations(scored, top_k=4)
    coarse = sum(1 for _, _, p in out
                 if p.payload["citation_precision"] == "section")
    assert len(out) == 4
    assert coarse == 2, "at top_k=4 the quota is half"


def test_the_best_coarse_passage_still_appears_first():
    """The cap limits how many, not whether. Suppressing the best match
    entirely would be a worse answer than one honest coarse citation."""
    from guidance.retrieval import _cap_coarse_citations

    scored = [(0.9, 0.9, _passage("isa-best", "section")),
              (0.4, 0.4, _passage("rcp-1", "recommendation")),
              (0.3, 0.3, _passage("rcp-2", "recommendation"))]

    out = _cap_coarse_citations(scored, top_k=3)
    assert out[0][2].entry_id == "isa-best"


def test_coarse_passages_are_kept_when_there_is_nothing_precise():
    """Returning fewer passages than exist would be a worse answer, and every
    coarse chunk already carries a caveat saying the citation is section-level."""
    from guidance.retrieval import _cap_coarse_citations

    scored = [(0.9 - i / 10, 0.9, _passage(f"isa{i}", "section"))
              for i in range(4)]
    out = _cap_coarse_citations(scored, top_k=4)
    assert len(out) == 4


def test_cap_does_not_disturb_a_corpus_with_no_coarse_passages():
    """The normal case: NICE and RCP only. Nothing should change."""
    from guidance.retrieval import _cap_coarse_citations

    scored = [(0.9 - i / 10, 0.9, _passage(f"p{i}", "recommendation"))
              for i in range(6)]
    out = _cap_coarse_citations(scored, top_k=4)
    assert [p.entry_id for _, _, p in out] == ["p0", "p1", "p2", "p3"]
