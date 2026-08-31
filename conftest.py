"""Makes the project's own packages importable during a test run.

`api`, `guidance`, `extraction`, `voice` and `models` are plain directories in
the repository root rather than an installed distribution. Under pytest's
default import mode the root is only placed on `sys.path` if a `conftest.py`
lives there — which is the entire reason this file exists.

Until now the suite passed because the development virtualenv happened to carry
an editable install of the project. That is a property of one machine, not of
the repository: rebuilding the virtualenv (or cloning the repo fresh) silently
removed it and every `from api...` import failed at collection. Doing it here
instead means the suite runs from a clean checkout with no install step.

Keep this file free of fixtures. Shared fixtures belong in `tests/conftest.py`,
which also neutralises the environment before collection so the suite never
reads your real `.env` or touches the real database. Putting fixtures here
would apply them to any future test tree outside `tests/` as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Prepend rather than append: if a same-named module is ever installed into
# site-packages, the checkout under test must win.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))