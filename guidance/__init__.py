"""RecoveryLens guidance layer.

Two distinct surfaces, deliberately kept apart:

  registry.py   Deterministic lookup for patient- and clinician-facing guidance
                keyed on the closed trigger set from Predictor._guidance().
                Cannot generate text, therefore cannot hallucinate.

  retrieval.py  Free-text clinician Q&A over the same corpus. This is the
                genuine retrieval surface. Its output is constrained to
                retrieved passages and it refuses when nothing relevant is found.

The safety argument rests on that separation: content a patient may act on is
never generated, and generated content is only ever read by a clinician.

Naming note
-----------
The singletons are exported as `guidance_registry` / `guidance_retriever`, not
as `registry` / `retriever`. Binding `registry` at package level would shadow the
`guidance.registry` submodule, so `import guidance; guidance.registry` would
return the object rather than the module - a confusing failure that only shows up
under reload or introspection. Import the modules by path if you need them.
"""

from .registry import (  # noqa: F401
    CANONICAL_TRIGGERS,
    GuidanceError,
    Registry,
    UnknownTrigger,
)
from .registry import registry as guidance_registry  # noqa: F401

__all__ = [
    "guidance_registry",
    "Registry",
    "CANONICAL_TRIGGERS",
    "GuidanceError",
    "UnknownTrigger",
]


def get_retriever():
    """Lazy accessor for the Q&A retriever.

    Deferred so that importing the package does not build a TF-IDF index. The
    API only needs it when someone actually asks a question, and keeping it out
    of import time keeps startup honest about what it is loading.
    """
    from .retrieval import retriever
    return retriever
