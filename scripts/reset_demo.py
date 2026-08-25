#!/usr/bin/env python3
"""
Wipe every trace of previous testing, ready for a live demo.

    python scripts/reset_demo.py

What it deletes
---------------
  recoverylens.db          patients, assessments, check-ins, ACCOUNTS, sessions,
                           and any generated audio still held in media_assets

What it deliberately KEEPS
--------------------------
  guidance/corpus.json     the guideline corpus. Regenerating it needs network
                           access to four publishers, one of which now 403s.
  guidance/embeddings.npz  774 cached vectors. Rebuilding costs an API call and,
                           if the key is missing on the day, leaves retrieval
                           silently degraded to TF-IDF — the worst possible
                           failure to discover in front of an audience.
  .env                     your keys.

Deleting the database also deletes every ACCOUNT, which is the point: the first
person to open the app creates a fresh one, and the sign-up screen is the first
thing shown. It also means nothing from earlier testing can appear on screen.
"""

from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "recoverylens.db"
TEST_DB = Path(tempfile.gettempdir()) / "recoverylens-tests.db"


def summarise(db: Path) -> str:
    """Say what is about to be destroyed, before destroying it."""
    if not db.exists():
        return "no database — nothing to clear"
    try:
        con = sqlite3.connect(db)
        counts = []
        for table, label in (("users", "account"), ("patients", "patient"),
                             ("check_ins", "check-in"),
                             ("media_assets", "audio file")):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                continue          # table predates this version
            if n:
                counts.append(f"{n} {label}{'s' if n != 1 else ''}")
        con.close()
        return ", ".join(counts) if counts else "already empty"
    except Exception as exc:
        return f"unreadable ({type(exc).__name__})"


def main() -> int:
    print(f"About to delete: {summarise(DB)}\n")

    for path, label in ((DB, "database"), (TEST_DB, "test database")):
        if path.exists():
            path.unlink()
            print(f"  removed {label:14} {path}")
        else:
            print(f"  no {label:17} (nothing to remove)")

    kept = []
    for path, why in ((ROOT / "guidance" / "corpus.json", "guideline corpus"),
                      (ROOT / "guidance" / "embeddings.npz", "cached embeddings"),
                      (ROOT / ".env", "your keys")):
        if path.exists():
            kept.append(f"  kept    {why:17} {path.name}")
    if kept:
        print()
        print("\n".join(kept))

    print("""
Done. The database is recreated empty when the server next starts.

Next:
  1. uvicorn api.main:app --reload --port 8000
  2. cd web && npm run dev
  3. open http://localhost:5173  ->  "Create an account"

Nothing from earlier testing can appear on screen. There are no accounts, so
the sign-up form is the first thing anyone sees.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
