"""RecoveryLens API package.

Loads .env before anything else in the package imports, so module-level code
reading os.getenv() sees the right values.

`import api.main` runs this file first, so this is guaranteed to happen before
anything main.py imports. The loader itself lives in the top-level config.py
rather than here, because the guidance CLIs need it too and they do not import
this package — see the note in config.py about the bug that caused.
"""

import sys
from pathlib import Path

# Repo root on the path, so `config` resolves when the app is launched as
# `uvicorn api.main:app` from anywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import load_env  # noqa: E402

load_env()
