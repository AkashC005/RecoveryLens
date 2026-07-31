# Guidance corpus — licensing and provenance

**Status: unresolved for public deployment. Read before deploying beyond a demo.**

## The issue

`guidance/corpus.json` quotes four clinical guidelines. Three are NICE
publications. NICE's site terms state:

> All content on this site is NICE copyright unless otherwise stated. You can
> download material for private research, study or in-house use only. Do not
> distribute or publish any material from this site without first obtaining
> NICE's permission.

A publicly reachable web service that renders NICE recommendation text is
plausibly "distribute or publish". A local demo on a laptop, or in-house use
within a single institution, plausibly is not. The distinction matters for
RecoveryLens because the deployment target is a public Render URL.

## What we do about it now

Three mitigations, all enforced in code rather than by convention:

1. **Short quotation, never reproduction.** `guidance/registry.py` sets
   `MAX_EXCERPT_WORDS = 60` and `validate()` refuses to load a corpus that
   breaches it. We never store a whole section.
2. **Attribution and deep link on every excerpt.** Each entry carries the
   publisher, guideline ID, section number and a URL back to the source. The
   product routes users to NICE rather than substituting for it.
3. **Provenance is visible, not buried.** `GET /api/guidance` publishes the full
   source list, including sources assessed and rejected.

This is a good-faith quotation posture. It is not permission.

## What still needs doing

- [ ] **Contact NICE** via <https://www.nice.org.uk/re-using-our-content> and
      describe the use: a non-commercial research prototype, quoting short
      attributed extracts with links, for a student competition. Ask whether the
      NICE Open Content Licence covers it.
- [ ] **Confirm ISA terms.** The ISA consensus statement is published openly at
      stroke-india.org but carries no explicit licence. Ask the Indian Stroke
      Association directly.
- [ ] **Decide the fallback.** If permission is not granted before demo day, the
      product still works with excerpts replaced by section titles plus links —
      the citation, without the quote. `registry.py` would need an
      `EXCERPTS_ENABLED` flag. This degrades the demo but removes the exposure.

## Note on the ISA scope contradiction

ISA 2024 §2.0 states rehabilitation is outside the document's scope, while §12.0
is titled "Stroke Rehabilitation". Every entry quoting §12.0 carries a `caveat`
recording this, and `tests/test_guidance.py::test_isa_rehab_entries_carry_the_scope_caveat`
fails if one is added without it. Do not present §12.0 material as a formal ISA
recommendation.

## Sources assessed and rejected

**ICMR Standard Treatment Workflow — Image Guided Management of Stroke.**
Assessed 2026-07-31. Content is confined to acute imaging, thrombolysis windows
and thrombectomy technique, and bears on none of the nine post-discharge
guidance triggers. Recorded in `sources.json` under `rejected_sources` so the
decision is auditable.
