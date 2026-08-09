# RecoveryLens — status

216 tests passing. Four new packages: `guidance/`, `triage/`, `messaging/`, `voice/`.

**Last verified:** 9 Aug 2026 — full 13-step walkthrough (`docs/WALKTHROUGH.md`)
run from an empty database. Steps 1–10 confirmed live, including a real WhatsApp
round trip. Steps 11–13 (opt-out, scheduler, voice) not yet re-run after the
STOP correction.

---

## Done

### Guidance layer

**Two-tier corpus — 291 passages.**
- `corpus.json` — 39 hand-verified excerpts with human-checked section numbers.
  Drives patient-facing cards, where a wrong citation is worst.
- `corpus_full.json` — 252 auto-ingested chunks from NICE NG236, NG128 and CG76
  via `python -m guidance.ingest`. Section numbers parsed from the source, never
  invented. Drives clinician Q&A only; carries no trigger, so the patient-facing
  path cannot reach it.

**Hybrid retrieval.** TF-IDF blended with OpenAI embeddings (60% semantic),
cached to `embeddings.npz`. Fixes vocabulary mismatch — asking "how do I prevent
falls?" against a corpus that says "recognise the complications ... including
frequent falls". Degrades to TF-IDF alone if the cache or key is missing.

**Measured refusal rate: 64% → 24% → 0%** on a 25-question probe set.
- 64% with 39 hand-typed excerpts
- 24% after ingestion (the overlap gate was also silently rejecting every
  single-keyword question — "what about spasticity?" has one content word)
- 0% after embeddings, with `EMBED_FLOOR = 0.28` measured via
  `python -m guidance.tune_floor`

**Marginal band.** Measurement showed no clean threshold, because the highest
out-of-scope scores are stroke-*adjacent*: "what dose of alteplase?" scores 0.379
because alteplase is a stroke drug and NG128 covers thrombolysis — the corpus
simply holds no dosing information. Rather than refuse 7 legitimate questions to
block 3 on-topic ones, questions between 0.28 and 0.42 are answered and flagged
`marginal`, with the answer stating plainly that the passages may not address
what was asked. Truly unrelated questions (meningitis 0.171, France 0.072) still
refuse outright.

**Agent-driven topic selection** — reads all 8 deficits (the old if/else read 4),
risk tiers and SHAP drivers, selects topics with a per-patient rationale.
Rule-selected topics remain a floor the agent cannot drop below.

**Patient-facing text stays verbatim and hand-verified.** The only thing still
deterministic, and deliberately so: the alternative is putting words in NICE's
mouth on the surface a carer reads unsupervised.

### Follow-up timeline
- Every check-in declares its evidence basis: `guideline`, `trial_convention`,
  or `operational`, with citations where they exist.
- Day 14's "Guideline-recommended post-discharge review" label was **removed** —
  no guideline in the corpus recommends a 14-day review. Day 90 relabelled as a
  trial outcome convention. Day 42 added, which two guidelines support.
- Days stay deterministic; only the clinician and caregiver narratives are
  generated, under separate grounding contracts.

### IST-3 external validation
| Outcome | IST-1 | IST-3 | Δ |
|---|---|---|---|
| `poor_outcome_6m` | 0.802 | 0.790 | −0.011 |
| `death_14d` | 0.793 | 0.736 | −0.057 |
| `haemorrhage_14d` | 0.732 | 0.601 | −0.131 |
| `nonadherence_6m` | 0.626 | 0.507 | −0.119 |

Models driven by clinical severity transport; those leaning on IST-1
trial-design artefacts (`RXASP`, `RXHEP`, `RCT` — absent from IST-3) collapse.
Calibration shifts (CITL −0.75) while ranking holds, which empirically justifies
the percentile-tier design over raw probabilities. `pe_14d` and
`recurrence_14d` are unvalidatable and documented as such.

### Conformal prediction — tested and rejected
Degraded calibration 12–42%; the models were already well calibrated internally.
Script and negative result kept, **not** wired into the API.

### Escalation triage agent
- Reads `free_text`, which was previously stored and never read.
- **Monotonic escalation** enforced in `TriageResult.finalise()`, not by prompt:
  the boolean rules always run and the agent can only add.
- Tools: risk profile, check-in history, guidance search, flag for clinician.
  No tool exists that clears an escalation.
- Clinician inbox shows rule vs agent reasons separately, plus the tool trace.

### WhatsApp messaging — VERIFIED LIVE, END TO END
The full round trip runs against real Twilio infrastructure:

    check-in sent to WhatsApp -> carer replies in plain English
      -> webhook (200 OK) -> triage agent reads it
      -> urgent escalation -> clinician inbox -> confirmation back to the carer

Verified with the message *"he's been more confused since Tuesday"* — all three
booleans unset, so the rule checks alone would have closed it as "nothing new".
The agent escalated it as **urgent**, and its recorded reasoning cited the
check-in history and the risk profile:

> a new, sudden-onset change in cognition with no prior baseline confusion in
> check-in history; combined with this patient's moderate recurrent-stroke risk
> and vigilance flags for haemorrhage/PE, this warrants urgent clinical review

- Pluggable sender; `ConsoleSender` is the default so a missing env var can never
  send real messages.
- Policy gate: opt-out → consent → usable number → rate limit. Every refusal
  explains itself.
- **Ambiguity escalates** — an unparseable reply defaults to concerning.
- STOP is permanent and outranks consent.
- Webhook with Twilio signature validation (confirmed rejecting unsigned requests).

### Voice (inbound)
- Voice note → transcribe → **read back** → confirm → only then recorded.
- Low ASR confidence escalates rather than being interpreted.
- **Negation-loss detection** — "he can't move his arm" transcribing as "he can
  move his arm" is the characteristic ASR error, and it runs in the reassuring
  direction. Fifteen such phrases are flagged.
- Unconfirmed transcripts escalate after 6h with the audio attached, rather than
  silently closing the check-in.

### Operational
- **`.env` loading in `config.py`**, called from `api/__init__.py`, `guidance/__init__.py`
  and every CLI. Originally lived only in `api/__init__.py`, which broke three
  separate things — the embeddings CLI, ad-hoc scripts, and test isolation — each
  presenting as a plausible but wrong result rather than an error.
- **Schema self-healing.** Additive columns are added automatically with data
  intact; only genuinely unsafe changes (dropped columns, type changes) raise.
  Three database wipes during development was two too many.
- **`tests/conftest.py`** neutralises every flag and credential before collection.
  Without it the suite inherited the developer's `.env` and made real, billable
  API calls — and had to set vars to `""` rather than delete them, because
  `load_env` runs later during collection with `override=False`.

### Scheduler
`messaging/scheduler.py` — APScheduler, off by default (`RECOVERYLENS_SCHEDULER=1`).
Polls every 15 min for due check-ins and sends each through the *same* policy gate
as the API; every 30 min escalates voice transcripts a carer never confirmed.
Never early, never twice, capped at 25 per run, `coalesce=True` so a laptop waking
from sleep does not fire a backlog of missed runs at once.

---

## Pending

### Quick (under an hour, no code)
- [ ] **Commit the working tree.** Uncommitted: the webhook `BackgroundTasks` fix,
      `_apply_rules_only`, the TwiML content-type fix, the `RetrievedPassage`
      schema fix, `messaging/scheduler.py`, `tests/test_scheduler.py`, and
      `docs/WALKTHROUGH.md`. The two most valuable bug fixes of the week are in
      here and only in here.
- [ ] **Finish walkthrough steps 11–13** — opt-out (via API, not WhatsApp STOP),
      scheduler, voice.
- [x] ~~Commit `guidance/embeddings.npz`~~ — tracked (1.4 MB, 291 vectors,
      `text-embedding-3-small`). A fresh clone now gets hybrid retrieval, not
      silently degraded TF-IDF.
- [x] ~~Twilio account setup~~ — done and verified live. Two gates that were not
      in the original guide and are now documented: trial accounts restrict
      recipients to Verified Caller IDs (572002), and paid accounts require an
      approved Trust Hub compliance profile (20003). Upgrading swaps one for the
      other rather than removing it.
- [x] ~~Citation spot-check~~ — done.

### Decisions for you
- [ ] **NICE copyright.** Short attributed quotation with deep links is a
      good-faith posture, not permission. See `GUIDANCE_LICENSING.md`. Matters
      before any public deployment.
- [ ] **`bleeding_warning_signs`** — source it properly, restrict it to a
      clinician prompt, or retire it. Options documented in `corpus.json`.

### Build remaining
- [ ] **Outbound voice (TTS).** `synthesise()` produces audio; nothing sends it.
      Twilio media messages need a publicly reachable URL, so this needs real
      hosting. Deliberately not half-built.
- [ ] **Translation (Tamil/Hindi).** Built English-only so translation quality
      could not mask voice bugs. Needs: translate the generated caregiver
      messages only (never quoted guideline text), a back-translation check, and
      a language field on `Patient`.
- [x] ~~Scheduler~~ — built, 17 tests, documented above.
- [ ] **Deployment.** `render.yaml` exists but has never been deployed. Note the
      free tier sleeps after 15 minutes — warm it before presenting. Deliberately
      last: everything else is testable locally, and a deployed copy of a moving
      target is wasted effort.

### Known limitations worth stating rather than hiding
- **WhatsApp 24-hour window — verified, not assumed.** Free-form messages are
  only permitted within 24h of the carer's last message. Outside it Twilio
  returns `[21654] ContentSid Required`: a pre-approved template is mandatory.
  An earlier version of the docs claimed the sandbox does not enforce this;
  testing against the live API disproved that. Every scheduled check-in past
  day 1 is outside the window by definition — the carer has not messaged you,
  which is precisely why you are messaging them — so production needs
  Meta-approved templates for the outbound prompt. The carer's reply then opens
  a window and everything downstream works free-form as built.
- **Voice is built and tested but not switched on in your `.env`.** `RECOVERYLENS_VOICE`
  is unset, so `NullSpeech` is active and voice notes are not transcribed. Set it
  before demonstrating that feature.
- **Voice is English-only.** Accuracy falls hardest on code-switched speech —
  English clinical terms inside Tamil or Hindi — which is what Indian carers
  actually use.
- **Models are from 1991–96 trial data**, predating routine thrombolysis. Tiers
  indicate relative priority, not calibrated probability. IST-3 validation
  confirms this empirically.
