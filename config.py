"""
RecoveryLens — config.py
========================
Loads .env, from anywhere.

Why this is not inside api/
---------------------------
It was, and that was a bug. `api/__init__.py` loaded .env, which worked for
`uvicorn api.main:app` and silently failed for everything else — the guidance
CLIs never import `api`, so `python -m guidance.embeddings` ran with no
environment at all. It reported "VOYAGE_API_KEY is not set" on a machine whose
.env had OPENAI_API_KEY sitting in it, because the file was never read.

That failure mode is the worst kind: a real, correct config, an accurate-sounding
error, and no indication the file was skipped. Any entry point that reads
environment variables calls load_env() first.

Shell variables always win over .env, so CI and one-off overrides still work:

    RECOVERYLENS_TRIAGE_AGENT=0 uvicorn api.main:app
"""

from __future__ import annotations

from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

_loaded = False


def load_env(path: Path | None = None, force: bool = False) -> bool:
    """Load .env if present. Returns whether a file was read.

    Idempotent — safe to call from every entry point, which is the point.
    """
    global _loaded
    if _loaded and not force:
        return True

    path = path or ENV_FILE
    if not path.exists():
        _loaded = True
        return False

    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        _loaded = True
        return True
    except ImportError:
        pass

    # Fallback parser, so a missing python-dotenv does not silently disable
    # every feature. Handles KEY=value, quotes, comments and blank lines, which
    # is all this project's .env contains.
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

    _loaded = True
    return True
