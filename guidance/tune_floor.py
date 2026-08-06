"""
RecoveryLens — guidance/tune_floor.py
=====================================
Measures the right refusal threshold for your corpus.

    python -m guidance.tune_floor

Why this exists as a script rather than a constant
--------------------------------------------------
SCORE_FLOOR (0.22) was measured: across a probe set the lowest legitimate
question scored 0.248 and the highest out-of-scope one 0.196, so 0.22 sits in
the gap. That number is only valid for the corpus and scoring it was measured
against.

EMBED_FLOOR could not be measured the same way — it was set without access to an
embedding API, so it is a guess. A guessed refusal threshold is the worst kind of
constant in this codebase: too low and out-of-scope questions get confident
answers, too high and the paraphrase cases embeddings were added to fix stay
refused. Either way it fails silently.

This script prints the separation and recommends a value.

Reading the output
------------------
A clean gap between the lowest in-scope and highest out-of-scope score means a
threshold exists. An OVERLAP means no single threshold separates them, and the
honest response is to prefer the safer side — set the floor above the highest
out-of-scope score and accept the refusals, rather than let a confidently
irrelevant recommendation reach a clinician.
"""

from __future__ import annotations

import sys

# Questions the corpus should be able to answer. Drawn from the sections the
# guidelines actually cover, including the paraphrase cases TF-IDF cannot reach.
IN_SCOPE = [
    "are wrist splints recommended?",
    "can they drive after a stroke?",
    "how do I support medication adherence?",
    "when should the six month review happen?",
    "what exercise is safe after stroke?",
    "how is dysphagia managed?",
    "should I refer for speech therapy?",
    "what about shoulder pain?",
    "how do I screen for depression after stroke?",
    "is constraint induced movement therapy useful?",
    "what about spasticity?",
    "how do I assess cognition?",
    "when can they return to work?",
    "what about incontinence?",
    "is fatigue common after stroke?",
    "should they have an orthoptist assessment?",
    "how often should blood pressure be checked?",
    "what about sexual function?",
    "is telerehabilitation effective?",
    "how do I manage mouth care?",
    "what support do carers need?",
    "should statins be continued?",
    "what about atrial fibrillation?",
    "how do I prevent falls?",
    "is aphasia treatable?",
]

# Questions the corpus must NOT answer. A mix of adjacent-but-wrong (dosing,
# other conditions) and plainly unrelated — the adjacent ones are the hard cases,
# because an embedding will find them somewhat similar to stroke content.
OUT_OF_SCOPE = [
    "what is the correct dose of alteplase?",
    "how do I manage a myocardial infarction?",
    "what are the surgical options for glioma?",
    "which chemotherapy for breast cancer?",
    "how do I repair an inguinal hernia?",
    "what antibiotics for meningitis?",
    "what is the capital of France?",
    "what is the best coffee in Chennai?",
    "how do I fix a car engine?",
]


def main() -> int:
    from .embeddings import _load_env_for_cli
    _load_env_for_cli()          # see config.py — CLIs must load .env themselves

    from .registry import Registry
    from .retrieval import CorpusRetriever

    retriever = CorpusRetriever(Registry.load(), score_floor=0.0)
    using_embeddings = retriever._embed_matrix is not None

    print(f"corpus     : {len(retriever.passages)} passages")
    print(f"scoring    : {'TF-IDF + embeddings' if using_embeddings else 'TF-IDF only'}")
    if not using_embeddings:
        print("\nEmbeddings are not active. Set RECOVERYLENS_EMBEDDINGS=1 and run\n"
              "`python -m guidance.embeddings` first — otherwise this measures\n"
              "SCORE_FLOOR, which is already tuned.")
    print()

    def top(q: str) -> float:
        hits = retriever.search(q, top_k=1, score_floor=0.0)
        return hits[0]["cosine"] if hits else 0.0

    ins = sorted((top(q), q) for q in IN_SCOPE)
    outs = sorted(((top(q), q) for q in OUT_OF_SCOPE), reverse=True)

    print("IN SCOPE (lowest first — these must be ANSWERED):")
    for score, q in ins:
        print(f"   {score:.3f}  {q}")
    print("\nOUT OF SCOPE (highest first — these must be REFUSED):")
    for score, q in outs:
        print(f"   {score:.3f}  {q}")

    lowest_in = ins[0][0]
    highest_out = outs[0][0]
    print(f"\nlowest in-scope    : {lowest_in:.3f}  ({ins[0][1]})")
    print(f"highest out-of-scope: {highest_out:.3f}  ({outs[0][1]})")

    if lowest_in > highest_out:
        recommended = round((lowest_in + highest_out) / 2, 3)
        print(f"\nCLEAN SEPARATION. Recommended floor: {recommended}")
        print(f"Set EMBED_FLOOR = {recommended} in guidance/retrieval.py")
        return 0

    # Overlap: no threshold separates them. Choose safety and say what it costs.
    safe = round(highest_out + 0.01, 3)
    lost = [q for s, q in ins if s < safe]
    print(f"\nOVERLAP — no single threshold separates these sets.")
    print(f"Safest floor: {safe} (above every out-of-scope question).")
    print(f"That refuses {len(lost)} of {len(IN_SCOPE)} legitimate questions:")
    for q in lost:
        print(f"   - {q}")
    print("\nPrefer this over a lower floor. A confidently irrelevant "
          "recommendation reaching a clinician is worse than a refusal, and the "
          "refusal message tells them what the corpus does cover.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
