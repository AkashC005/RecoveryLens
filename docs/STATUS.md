# RecoveryLens — status

273 tests passing. Four new packages: `guidance/`, `triage/`, `messaging/`, `voice/`.

**Last verified:** 9 Aug 2026 — full 13-step walkthrough (`docs/WALKTHROUGH.md`)
run from an empty database. Steps 1–10 confirmed live, including a real WhatsApp
round trip. Steps 11–13 (opt-out, scheduler, voice) not yet re-run after the
STOP correction.

---

## Done

### Guidance layer

**One corpus file, two provenance levels.** `corpus.json` holds both:
- `triggers[*].entries` — hand-verified excerpts, human-checked section numbers.
  The only passages the patient-facing cards can reach.
- `chunks` — auto-extracted via `python -m guidance.ingest`. Section numbers
  parsed from the source, never invented. Clinician Q&A only.

The line between them is the `extraction` field, and `registry.py::validate()`
enforces it — an auto-extracted entry appearing under `triggers` fails at import.
It used to be enforced by ingested chunks having no `trigger`, which was a
guarantee living in a missing value, and it broke the moment something needed
`trigger` to be non-null (a 500 on the Ask box).

**Sources: NICE NG236, NG128, CG76, RCP 2023, ISA 2024.** RCP was already a
registered source cited for follow-up cadence and had never been ingested — it
publishes one page per chapter as static HTML with the guideline's own lettered
numbering (`5.7 A`), and its section list reads like a description of this
product: *2.13 Follow-up review*, *2.16 Carers*, *4.21 Falls*, *4.24 Spasticity*,
*4.25 Fatigue*, and the whole of *5.x* secondary prevention. One chapter alone
holds 56 lettered recommendations.

**Citation precision is a field, not an assumption.** `citation_precision` is
`recommendation` for NICE and RCP, and `section` for ISA — because ISA numbers
sections but presents recommendations as unnumbered bullets, so `§12.0` is the
finest reference the document supports. Those chunks carry a caveat saying so,
and `_cap_coarse_citations` limits them to half the passages in any answer. An
answer can cite ISA; it cannot rest entirely on citations a reader cannot check
precisely.

**Per-chunk deep links.** Chunks record the page they came from. Without it every
RCP citation would have pointed at the front page of strokeguideline.org, and a
citation nobody can follow is decoration.

**The footnote stripper nearly shipped a clinical error.** ISA is a PDF, and PDF
extraction glues reference markers onto sentences (`stroke survivors.27`). The
first stripping regex turned *"administered within 4.5 hours of symptom onset"*
into *"within 4. hours"* — a silently altered drug window, quoted verbatim with a
citation attached. `_strip_refs` now verifies its own output: if any decimal or
ratio quantity present before is missing after, the strip is abandoned and the
original text kept. A visible `survivors.27` is cosmetic; `4. hours` is not.
Seven parametrised tests pin the quantities that must survive.

**Hybrid retrieval.** TF-IDF blended with OpenAI embeddings (60% semantic),
cached to `embeddings.npz`. Fixes vocabulary mismatch — asking "how do I prevent
falls?" against a corpus that says "recognise the complications ... including
frequent falls". Degrades to TF-IDF alone if the cache or key is missing.

**Expanding the corpus caused a safety regression, and measurement caught it.**
Both refusal floors were measured at 291 passages and both became unsafe at 774:

| | measured at 291 | measured at 774 | highest out-of-scope |
|---|---|---|---|
| `EMBED_FLOOR` (blended) | 0.28 | **0.405** | alteplase 0.392, MI 0.351, glioma 0.282 |
| `SCORE_FLOOR` (TF-IDF only) | 0.22 | **0.24** | MI 0.222 |

At the old floors the retriever would have answered *"how do I manage a myocardial
infarction?"* with stroke recommendations and a citation. Adding coverage raises
**every** score, including the ones that must be refused — the specific hazard of
growing a corpus, and why these numbers are measured rather than chosen. A test in
the suite failed on it before anything else was touched.

**Final state: CLEAN SEPARATION.** Lowest in-scope 0.418 ("what about sexual
function?"), highest out-of-scope 0.392 (alteplase). Margin 0.026 — narrow but
real, and it did not come from lowering the floor. Two fixes closed it:

1. **A query-side synonym.** "Should statins be continued?" was the lowest
   in-scope score at 0.366 and the only question the floor would have refused,
   while the corpus held NG128 1.4.22 (*"Continue statin treatment in people with
   acute stroke who are already receiving statins"*) and RCP 5.5 B. The answer was
   there; the words were not. `statin → lipid` moved it to **0.436, a gain of
   0.070**.
2. **Duplicate suppression** — 803 → 774 passages, removing 29 curated
   recommendations that ingestion had re-extracted and that were competing with
   their own hand-verified copies.

`EMBED_CONFIDENT` widened 0.42 → 0.44, so the five in-scope questions closest to
the ceiling (0.418–0.436) are answered *with* the marginal note. Five hedges out
of 25 is more than before and correct: with 0.026 of separation, a question low in
the in-scope range genuinely is close to one that would be refused.

Alteplase stays high because it *is* a stroke drug and both NG128 and RCP 3.5
cover thrombolysis. The corpus holds indications, timing and service requirements
but **no dosing at all** — `mg/kg` appears in zero chunks. A category error, not
an irrelevance, and the probe label was re-verified rather than assumed.

**TF-IDF-only mode is now materially weaker, not just slightly.** Raising
`SCORE_FLOOR` to 0.24 to keep the MI question out also pushed NG236 1.8.1
("Offer ... a specialist orthoptist assessment") below the floor — a correct
answer to a legitimate question, now unreachable without embeddings. Recorded in
`test_tfidf_only_mode_is_measurably_weaker_now`, which fails if it ever becomes
retrievable again. Embeddings were an optional improvement at 291 passages; at
803 they are load-bearing.

**29 of 35 curated entries were duplicated by ingested chunks.** Ingestion
re-reads the documents a human already read, so the index held two copies of
NG236 1.8.2 and a "top 3" could show one recommendation twice — reading as though
two guidelines agreed when it was one, quoted twice. The curated copy now wins,
because it is hand-verified and carries any caveat a human attached.

**One expectation legitimately moved.** "What exercise can they do at home?" used
to expect NG236 1.13.5, which in full is *"Encourage people to participate in
physical activity after stroke."* — nine words answering neither part of the
question. RCP 5.23 A now wins, with 5.23 E on tailoring and 5.23 G on community
facilities behind it. The old expectation was pinned to the best a NICE-only
corpus could manage.

**Refusal rate on the 25-question probe set: 64% → 24% → 0%, and still 0%.**
- 64% with 39 hand-typed excerpts
- 24% after the first ingestion (the overlap gate was also silently rejecting
  every single-keyword question — "what about spasticity?" has one content word)
- 0% after embeddings, at 291 passages
- **still 0% at 774 passages**, now with a floor *above* every out-of-scope
  question rather than below the stroke-adjacent ones. That is a stronger claim
  than the earlier 0%: the same coverage with a stricter refusal boundary.

**Marginal band.** Measurement still shows no clean threshold, and the reason is
unchanged: the highest out-of-scope scores are stroke-*adjacent*. "What dose of
alteplase?" scores 0.392 because alteplase is a stroke drug and both NG128 and RCP
3.5 cover thrombolysis — the corpus holds indications, timing and service
requirements but **zero** dosing (`mg/kg` appears in no chunk). That is a category
error, not an irrelevance.

The policy changed with the numbers, though. At 291 passages the band was wide
(0.28–0.42) and the floor sat *below* the adjacent cluster, answering those
questions with a hedge. At 803 the adjacent cluster rose far enough that a floor
below it would answer *"how do I manage a myocardial infarction?"*, so the floor
now sits **above** every out-of-scope question (0.402) and the band above it
(0.402–0.44) is narrower and exists to hedge the 0.010 margin rather than to
rescue on-topic questions. Truly unrelated questions (meningitis 0.180, France
0.075) refuse by a wide margin either way.

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

### Patient record
`GET /api/patients/{id}` existed, was served, and nothing in the frontend called
it — the row in the patient list was not clickable. It now returns a declared
`PatientDetail` shape rather than an unvalidated dict, and adds the three things
that were missing and mattered:

- **Assessment inputs** alongside results. A risk tier with no visible input is
  not reviewable; a clinician disagreeing with a tier needs to see whether the
  model or the data is wrong.
- **The full triage record per check-in** — rule reasons, agent reasons, urgency
  and tool trace, kept separate exactly as the inbox shows them. `AgentTrace` is
  now one shared component, because two screens rendering triage two different
  ways is how a reviewer ends up trusting one and not the other.
- **Messaging state**, at the top of the screen. This is the only place a
  clinician can see that follow-up has silently stopped. `can_send` and
  `blocked_reason` are the return value of the same `may_send()` call the send
  endpoint makes — never re-derived, or the screen would eventually promise a
  send the gate refuses.

Two derived fields worth naming. Check-in `status` separates **`overdue`** (the
date passed and nothing went out — scheduler off, consent missing, rate limited;
ours to fix) from **`sent`** (the carer has not replied; theirs). One string for
both would have hidden the actionable half. And `whatsapp_window_open` is
computed from `last_inbound_at`, so the 24-hour constraint is visible *before* a
send fails with `[21654]` rather than after.

The full phone number is deliberately not returned — last four digits only. This
is the screen that gets demonstrated on a projector; the full number is in the
database and in the send preview, which is where it is actually needed. A test
asserts it never appears in the response body.

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
- **`DATABASE_URL` is overridden, not cleared.** It used to sit in the cleared
  list, which was worse than leaving it alone: `api/database.py` reads it as
  `os.getenv("DATABASE_URL", "sqlite:///./recoverylens.db")`, so an *absent*
  variable falls through to the real demo database in the repository root. Any
  test touching the API would have read and written live data. Clearing a
  variable is only safe when absence means "off"; here absence meant "use
  production". It now points at a throwaway file in the temp directory, deleted
  at the start of each session. Found while writing the first HTTP-level tests —
  nothing had exercised that path before.
- **First tests through the HTTP layer** (`tests/test_patients.py`, 18 tests).
  Every other test file exercises a package directly, which is why the two most
  expensive bugs of the project — `RetrievedPassage.trigger` rejecting null, and
  the webhook timing out — were both found by hand instead. The client
  deliberately skips the startup hook, which asserts something worth asserting:
  the patient endpoints must not depend on `predictor.load()` unpickling model
  artifacts, and must not start the background scheduler.

### Scheduler
`messaging/scheduler.py` — APScheduler, off by default (`RECOVERYLENS_SCHEDULER=1`).
Polls every 15 min for due check-ins and sends each through the *same* policy gate
as the API; every 30 min escalates voice transcripts a carer never confirmed.
Never early, never twice, capped at 25 per run, `coalesce=True` so a laptop waking
from sleep does not fire a backlog of missed runs at once.

---

## Pending

### Quick (under an hour, no code)
- [x] ~~Run the expanded ingestion~~ — done. 764 chunks (was 252): NG236 146,
      NG128 64, CG76 42, **RCP 512**. Floors re-measured, see above.
- [x] ~~Re-measure the floors~~ — done. Clean separation at 774 passages,
      `EMBED_FLOOR = 0.405`, **0 of 25 probe questions refused**.
- [ ] **ISA never ingested** — `stroke-india.org` returns 403 to a programmatic
      request; it serves the PDF to a browser and refuses an unrecognised client.
      Rather than disguise the request, ingestion now looks on disk first:
      download it once to `guidance/sources/isa_2024.pdf` (see the README there)
      and re-run. Until then there are **zero** section-precision chunks, so
      `_cap_coarse_citations` is covered by unit tests only and has never run on
      real data.
- [ ] **Finish walkthrough steps 11–13** — opt-out (via API, not WhatsApp STOP),
      scheduler, voice.
- [x] ~~Commit the working tree~~ — done.
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
