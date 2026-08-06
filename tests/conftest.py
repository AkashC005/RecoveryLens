"""
Test isolation.

Tests must not read .env. Without this they do — `config.load_env()` is called
by anything that imports the api package, and once .env is loaded the suite
inherits RECOVERYLENS_TRIAGE_AGENT=1, RECOVERYLENS_GUIDANCE_AGENT=1 and live API
keys from the developer's machine.

The consequences are all bad and none of them are obvious:

  - tests make REAL API calls, so they cost money and need network
  - runtime goes from seconds to minutes
  - results depend on what a model returned that day, so a passing suite proves
    nothing and a failing one may be a rate limit
  - a test that should exercise the rules-only path silently exercises the agent

Every feature in this codebase is off by default and degrades to deterministic
behaviour. That default is what the tests are written against, so the suite runs
with the environment cleared and each test opts in explicitly via monkeypatch —
which is exactly how test_triage.py and test_selector.py already do it.
"""

import os

import pytest

# Cleared for every test. Feature flags first, then credentials — a test that
# gets past a flag check must still not be able to reach a provider.
ISOLATED_VARS = [
    # feature flags
    "RECOVERYLENS_TRIAGE_AGENT",
    "RECOVERYLENS_GUIDANCE_AGENT",
    "RECOVERYLENS_LLM_SYNTHESIS",
    "RECOVERYLENS_EMBEDDINGS",
    "RECOVERYLENS_VOICE",
    "RECOVERYLENS_MESSAGING",
    # credentials
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "TWILIO_WEBHOOK_URL",
    # provider/model overrides
    "RECOVERYLENS_EMBED_PROVIDER",
    "RECOVERYLENS_EMBED_MODEL",
    "RECOVERYLENS_LLM_MODEL",
    "RECOVERYLENS_STT_MODEL",
    "RECOVERYLENS_TTS_MODEL",
    "RECOVERYLENS_TTS_VOICE",
    # never point a test at a real database
    "DATABASE_URL",
]


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    """Autouse: applies to every test without being requested.

    monkeypatch restores the previous values afterwards, so this does not
    disturb anything outside the test session.
    """
    for var in ISOLATED_VARS:
        monkeypatch.delenv(var, raising=False)


def pytest_configure(config):
    """Clear the environment BEFORE collection.

    The autouse fixture above is not sufficient on its own. `guidance/__init__.py`
    loads .env on import, and importing `guidance.retrieval` builds the module
    level `retriever` singleton — both of which happen during collection, before
    any fixture runs. Without clearing here, that singleton would be constructed
    with embeddings enabled and would load the developer's cache, so the suite
    would test a configuration that only exists on one machine.

    pytest_configure runs before collection, which is early enough.
    """
    # Set to empty rather than deleting. `config.load_env()` uses override=False
    # and runs when `guidance` is imported during collection — AFTER this hook.
    # A deleted var is therefore "not in os.environ" and gets repopulated from
    # .env; an empty one is present, so .env leaves it alone. Every flag check in
    # the codebase treats "" as off.
    for var in ISOLATED_VARS:
        os.environ[var] = ""
    print("\n[tests] environment neutralised before collection — the suite runs "
          "against the deterministic defaults, not your .env.")
