"""
RecoveryLens — extraction
=========================
Reading a discharge summary into the assessment form.

This is STRUCTURED EXTRACTION, not retrieval. One document, a fixed schema of
known fields. Calling it RAG would lead to building the wrong thing and
evaluating it the wrong way: RAG searches a corpus to answer an open question,
whereas here the questions are fixed and the corpus is one page.

The three rules, which are the same discipline as the rest of this codebase
------------------------------------------------------------------------------
1. EVERY VALUE CARRIES THE SPAN IT CAME FROM. A field with no verbatim
   supporting quote from the document is not extracted. This is the guidance
   corpus rule — quote or refuse — applied to a different problem, and it is what
   makes the output auditable rather than merely plausible.

2. NOT FOUND MEANS BLANK, NOT GUESSED. A model that fills `systolic_bp: 140`
   because 140 is a typical value is worse than one that leaves it empty. The
   blank costs a clinician five seconds; the guess costs a wrong risk tier that
   nobody knows to question.

3. NOTHING IS SUBMITTED. Extraction PRE-FILLS the form and marks every machine-
   filled field until a human confirms it. This is the voice read-back pattern:
   a person verifying a machine's reading before it counts. Reusing it means one
   idea in the product rather than two.

Why an LLM rather than a trained extractor
------------------------------------------
There is no model trained on this schema. Clinical NER (cTAKES, medspaCy,
scispaCy) finds entities — "aspirin", "hypertension" — but mapping those onto
`planned_aspirin: true` is the actual work and is left undone. A supervised
extractor would need a few hundred annotated summaries, which means a hospital
partner and ethics approval.

The published comparison also favours this route at our scale: fine-tuned models
beat prompting by roughly 1% where data is plentiful, and lose by around 7% F1
where it is scarce. Scarce is where we are.
"""

from .extract import (
    ExtractedField,
    Extraction,
    EXTRACTABLE_FIELDS,
    extract_from_text,
    extraction_enabled,
    text_from_pdf,
)

__all__ = [
    "ExtractedField", "Extraction", "EXTRACTABLE_FIELDS",
    "extract_from_text", "extraction_enabled", "text_from_pdf",
]
