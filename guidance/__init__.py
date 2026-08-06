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

import sys as _sys
from pathlib import Path as _Path

# Load .env on package import, not just from the CLIs.
#
# This was the second time the same bug bit: config was loaded by api/__init__.py
# and by each guidance CLI, but NOT by the package itself. So any script doing
#
#     from guidance.retrieval import retriever
#
# ran with no environment — embeddings silently disabled, RECOVERYLENS_* flags
# all unset — and produced results that looked plausible and were wrong. The API
# worked, the CLIs worked, ad-hoc scripts quietly did not.
#
# Loading here covers every import path into the package. Tests clear these vars
# in tests/conftest.py before collection, so the suite still runs against the
# deterministic defaults.
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
try:
    from config import load_env as _load_env
    _load_env()
except ImportError:      # pragma: no cover - config.py should always be present
    pass

from .registry import (  # noqa: F401,E402
    CANONICAL_TRIGGERS,
    GuidanceError,
    Registry,
    UnknownTrigger,
)
from .registry import registry as guidance_registry  # noqa: F401,E402

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
