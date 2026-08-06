"""
RecoveryLens — guidance/embeddings.py
=====================================
Semantic search over the guidance corpus, via an embedding API.

The problem TF-IDF cannot solve
-------------------------------
After ingestion the corpus covers 291 chunks and refusal fell from 64% to 24%.
The six questions still refused are not missing content — they are vocabulary
mismatches:

    asked:  "how do I prevent falls?"
    corpus: "recognise the complications ... including frequent falls"

    asked:  "can they drive after a stroke?"
    corpus: "when advising people with visual problems about driving, consult
             the DVLA regulations"

TF-IDF matches surface form. No threshold tuning reaches these, because the
words genuinely differ. Embeddings match meaning, which is exactly the gap.

Why an API rather than sentence-transformers
--------------------------------------------
sentence-transformers pulls torch — roughly 2GB against a 512MB deploy target.
An embedding API is one HTTP call and a cached array. Same benefit, none of the
footprint. This was the right call at 39 chunks too; the difference now is that
there is enough corpus for semantics to matter.

Provider
--------
Voyage by default. Anthropic does not offer an embeddings API and recommends
Voyage as its partner; since this project already runs on Claude, that is the
path of least friction. OpenAI is supported for anyone who has that key instead.

ASYMMETRIC EMBEDDING — the detail that is easy to miss and costs real quality.
Voyage embeds documents and queries differently: a corpus chunk must be embedded
with input_type="document" and a question with input_type="query". Use the same
type for both and retrieval quality drops for no visible reason, because nothing
errors. `embed_texts` takes the type explicitly rather than defaulting, so a
caller has to decide which it means.

Hybrid, not replacement
-----------------------
Both signals are kept. TF-IDF is better at exact clinical terms — "hemianopia",
"ASPECTS", a section number — where an embedding may blur distinctions that
matter. Embeddings are better at paraphrase. Blending gets both; see
`BLEND_WEIGHT` for the measured weighting.

Caching
-------
Embeddings are computed once by `python -m guidance.embeddings` and cached to
disk, keyed by a hash of the chunk text. The API never embeds the corpus at
startup: 291 calls on every uvicorn boot would be slow, costly, and would make
the app depend on a network round trip to serve a request it can already answer.
A chunk whose text changes gets a new hash and is re-embedded; unchanged chunks
are reused.

Degradation
-----------
No key, no cache, or an API failure leaves the retriever on TF-IDF alone. The
product never depends on this being available.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / "embeddings.npz"

# voyage-3.5-lite is ample for 291 short clinical sentences and sits inside the
# free tier. voyage-3-large scores higher on general benchmarks but the gain does
# not show on a corpus this size and this focused.
DEFAULT_MODEL = {"voyage": "voyage-3.5-lite", "openai": "text-embedding-3-small"}
BATCH = 64

# Measured over the probe set. Embeddings carry more weight because the failures
# they fix are the ones TF-IDF structurally cannot, but TF-IDF is kept
# meaningfully in the blend so exact clinical terms still dominate when present.
BLEND_WEIGHT = 0.6          # weight on the embedding score


def embeddings_enabled() -> bool:
    return (os.getenv("RECOVERYLENS_EMBEDDINGS", "").strip().lower()
            in {"1", "true", "yes", "openai"})


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _load_env_for_cli() -> None:
    """Read .env when this module is run directly.

    Shared by every guidance CLI. `python -m guidance.embeddings` does not
    import the api package, so nothing else loads the file.
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from config import load_env
        load_env()
    except ImportError:
        pass


def provider() -> str:
    """'voyage' or 'openai'. Voyage unless OpenAI is explicitly asked for."""
    name = os.getenv("RECOVERYLENS_EMBED_PROVIDER", "").strip().lower()
    if name in {"voyage", "openai"}:
        return name
    # Infer from whichever key is present; Voyage wins a tie.
    if os.getenv("VOYAGE_API_KEY", "").strip():
        return "voyage"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai"
    return "voyage"


def default_model(name: str | None = None) -> str:
    name = name or provider()
    return os.getenv("RECOVERYLENS_EMBED_MODEL", "").strip() or DEFAULT_MODEL[name]


def embed_texts(texts: list[str], input_type: str,
                model: str | None = None) -> np.ndarray:
    """Embed texts. Returns (n, dim) float32, L2-normalised.

    `input_type` must be "document" or "query" and is REQUIRED, not defaulted.
    Voyage embeds the two asymmetrically, and using the wrong one degrades
    retrieval silently — nothing errors, results just get quietly worse. Making
    the caller state which it means is cheap insurance against that.
    """
    if input_type not in {"document", "query"}:
        raise ValueError(f"input_type must be 'document' or 'query', got {input_type!r}")

    name = provider()
    model = model or default_model(name)
    prepared = [t.replace("\n", " ")[:8000] for t in texts]
    vectors: list[list[float]] = []

    if name == "voyage":
        key = os.getenv("VOYAGE_API_KEY", "").strip()
        if not key:
            raise RuntimeError("VOYAGE_API_KEY is not set.")
        import voyageai
        client = voyageai.Client(api_key=key)
        for i in range(0, len(prepared), BATCH):
            resp = client.embed(prepared[i:i + BATCH], model=model,
                                input_type=input_type)
            vectors.extend(resp.embeddings)
    else:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        from openai import OpenAI
        client = OpenAI(api_key=key)
        # OpenAI embeddings are symmetric; input_type is accepted and ignored.
        for i in range(0, len(prepared), BATCH):
            resp = client.embeddings.create(model=model, input=prepared[i:i + BATCH])
            vectors.extend(d.embedding for d in resp.data)

    arr = np.asarray(vectors, dtype=np.float32)
    # Normalise once here so similarity is a plain dot product later.
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.clip(norms, 1e-9, None)


class EmbeddingIndex:
    """Cached corpus embeddings, looked up by content hash."""

    def __init__(self, vectors: dict[str, np.ndarray], model: str = ""):
        self.vectors = vectors
        self.model = model

    # ------------------------------------------------------------------ disk
    @classmethod
    def load(cls, path: Path = CACHE) -> "EmbeddingIndex | None":
        if not path.exists():
            return None
        try:
            data = np.load(path, allow_pickle=False)
            keys = [str(k) for k in data["keys"]]
            mat = data["vectors"]
            model = str(data["model"]) if "model" in data else ""
            return cls({k: mat[i] for i, k in enumerate(keys)}, model=model)
        except Exception as exc:
            print(f"[embeddings] cache unreadable ({type(exc).__name__}); "
                  f"falling back to TF-IDF only.")
            return None

    def save(self, path: Path = CACHE) -> None:
        keys = list(self.vectors)
        mat = np.vstack([self.vectors[k] for k in keys]) if keys else np.zeros((0, 0))
        np.savez_compressed(path, keys=np.array(keys), vectors=mat,
                            model=np.array(self.model))

    # ---------------------------------------------------------------- lookup
    def matrix_for(self, texts: list[str]) -> np.ndarray | None:
        """Vectors aligned to `texts`, or None if any are missing.

        All-or-nothing on purpose. A partially embedded corpus would score some
        passages semantically and others at zero, quietly ranking the embedded
        ones above the rest for reasons unrelated to relevance.
        """
        rows = []
        for t in texts:
            vec = self.vectors.get(text_hash(t))
            if vec is None:
                return None
            rows.append(vec)
        return np.vstack(rows) if rows else None

    def embed_query(self, query: str) -> np.ndarray | None:
        """Embed a question. input_type="query" — see the note in the header on
        why this must differ from how the corpus was embedded."""
        try:
            return embed_texts([query], input_type="query",
                               model=self.model or None)[0]
        except Exception as exc:
            print(f"[embeddings] query embedding failed "
                  f"({type(exc).__name__}); TF-IDF only for this request.")
            return None


# --------------------------------------------------------------------------- #
def build(texts: list[str], model: str | None = None,
          existing: EmbeddingIndex | None = None) -> EmbeddingIndex:
    """Embed any corpus text not already cached. Unchanged chunks are reused."""
    model = model or default_model()
    vectors = dict(existing.vectors) if existing else {}

    # A cache built with a different model is not reusable — dimensions and
    # geometry both differ. Start fresh rather than mixing two vector spaces,
    # which would rank passages by which model embedded them.
    if existing and existing.model and existing.model != model:
        print(f"model changed ({existing.model} -> {model}); re-embedding all")
        vectors = {}

    todo = [t for t in texts if text_hash(t) not in vectors]
    unique: dict[str, str] = {text_hash(t): t for t in todo}

    if unique:
        print(f"embedding {len(unique)} new chunks "
              f"({len(texts) - len(unique)} reused) … ", end="", flush=True)
        items = list(unique.items())
        # input_type="document": these are corpus passages, not questions.
        arr = embed_texts([t for _, t in items], input_type="document", model=model)
        for (h, _), vec in zip(items, arr):
            vectors[h] = vec
        print("done")
    else:
        print(f"all {len(texts)} chunks already cached")

    return EmbeddingIndex(vectors, model=model)


def main() -> int:
    """python -m guidance.embeddings — build or refresh the cache."""
    # Without this the CLI runs with no .env at all: only api/__init__.py used
    # to load it, and this module never imports api. See config.py.
    _load_env_for_cli()

    name = provider()
    key_var = "VOYAGE_API_KEY" if name == "voyage" else "OPENAI_API_KEY"
    if not os.getenv(key_var, "").strip():
        print(f"{key_var} is not set. Nothing to do.\n"
              f"Retrieval works without embeddings, just less well on paraphrase.\n"
              f"Voyage keys: https://dash.voyageai.com — Anthropic's recommended\n"
              f"embeddings partner, with a free tier.",
              file=sys.stderr)
        return 1
    print(f"provider: {name} / {default_model(name)}")

    # Import here so this module stays importable without the retriever.
    from .retrieval import CorpusRetriever
    from .registry import Registry

    retriever = CorpusRetriever(Registry.load())
    texts = [p.text for p in retriever.passages]
    print(f"corpus: {len(texts)} passages "
          f"({retriever.curated_count} curated + {retriever.ingested_count} ingested)")

    try:
        index = build(texts, existing=EmbeddingIndex.load())
    except Exception as exc:
        # A billing or auth failure is a configuration problem, not a bug, and a
        # stack trace makes it look like the latter.
        text = str(exc).lower()
        print()
        if "credit" in text or "quota" in text or "insufficient" in text:
            print("No API credits on this account.", file=sys.stderr)
            print("  OpenAI: add credit at "
                  "platform.openai.com/settings/organization/billing\n"
                  "          (the $5 free grant expires after a year, so older\n"
                  "           accounts often show this)\n"
                  "  Or switch to Voyage, which has a free tier:\n"
                  "      RECOVERYLENS_EMBED_PROVIDER=voyage\n"
                  "      VOYAGE_API_KEY=...   (dash.voyageai.com)",
                  file=sys.stderr)
        elif "authentication" in text or "api key" in text or "401" in text:
            print(f"Key rejected by {name}. Check it is current and has not been "
                  f"revoked.", file=sys.stderr)
        else:
            print(f"Embedding failed: {type(exc).__name__}: {exc}", file=sys.stderr)

        print("\nNothing was written. Retrieval continues on TF-IDF alone — "
              "refusal sits at 24% rather than lower.", file=sys.stderr)
        return 1

    index.save()

    size_kb = CACHE.stat().st_size / 1024
    print(f"\nWrote {CACHE} — {len(index.vectors)} vectors, {size_kb:.0f} KB")
    print("Set RECOVERYLENS_EMBEDDINGS=1 to use them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
