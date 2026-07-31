# RecoveryLens — guidance layer verification

Ordered cheapest-first, so failures surface before you spend an API call or wait
on a frontend build. Each step states what PASS looks like. Total ~20 minutes.

Run everything from the repo root unless stated otherwise.

---

## Step 0 — Environment (2 min)

Install the **API** requirements, not the training ones:

```bash
cd ~/Desktop/"Recovery Lens"
source .venv/bin/activate
pip install -r requirements-api.txt
pip install pytest
```

**PASS:** no errors. `python -c "import sklearn; print(sklearn.__version__)"` prints
`1.8.0`.

> **Do not use `requirements.txt` for verification.** That file is the model
> *training* environment and pulls catboost, xgboost and lightgbm, none of which
> the API or the guidance layer imports. catboost in particular has no wheel for
> Python 3.14, so pip falls back to compiling from source and fails with
> `KeyError: 'VERSION'` in `get_catboost_version`. You only need
> `requirements.txt` if you are re-running `ml/RecoveryLens_ML_Pipeline.ipynb`,
> and that needs Python ≤ 3.13.

> If sklearn prints `1.5.2`, your venv predates the pin fix — reinstall. This
> matters: the saved models were pickled by 1.8.0, and loading them under a
> different minor version can produce silently wrong predictions rather than an
> error.

### Python version

`render.yaml` pins the deployment to **Python 3.12**. Check what you are on:

```bash
python --version
```

If your venv is 3.13 or 3.14, the guidance layer will still work — it is pure
Python plus scikit-learn. But you are then testing against a different runtime
than you deploy on, and the model pickles are the risky part. If `predictor.load()`
fails or throws warnings at Step 3, rebuild the venv on 3.12:

```bash
deactivate && rm -rf .venv
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-api.txt pytest
```

---

## Step 1 — Test suite (1 min, no API key needed)

```bash
python -m pytest tests/test_guidance.py -v
```

**PASS:** `37 passed`.

Worth knowing what a few of these actually protect:

| Test | Guards against |
|---|---|
| `test_predictor_triggers_match_the_corpus` | Model emitting a trigger with no content behind it |
| `test_missing_api_key_falls_back_to_extractive_not_a_lie` | Response claiming "synthesised" when no model ran |
| `test_only_retrieved_passages_reach_the_model` | Unretrieved corpus text leaking into the prompt |
| `test_no_excerpt_exceeds_the_copyright_cap` | Quoting more of NICE than fair quotation allows |
| `test_retriever_refuses_out_of_scope` | Answering questions the corpus cannot address |

---

## Step 2 — Corpus health (30 sec, no API key)

```bash
python -c "
from guidance import guidance_registry as g
import json; print(json.dumps(g.coverage_report(), indent=2))"
```

**PASS:** `"coverage": "8/9"`, `"total_entries": 39`,
`"evidence_gaps": ["bleeding_warning_signs"]`, four sources listed.

---

## Step 3 — Start the API (1 min)

```bash
uvicorn api.main:app --reload --port 8000
```

**PASS:** startup prints
`Loaded 6 models. Guidance corpus: 8/9 triggers covered, 39 cited entries.`
followed by `Documented evidence gaps: bleeding_warning_signs` and `Ready.`

> If it crashes on `from guidance import ...`, you are not running from the repo
> root. `guidance/` must sit beside `api/`, not inside it.

Leave this running. Open <http://localhost:8000/docs> in a second terminal/tab.

---

## Step 4 — Guidance endpoints (3 min, no API key)

In the Swagger UI at `/docs`, or by curl:

```bash
# 4a. A covered trigger — expect real quoted guideline text
curl -s localhost:8000/api/guidance/visual_field_safety | python -m json.tool

# 4b. The evidence gap — expect 200, empty entries, a real explanation
curl -s localhost:8000/api/guidance/bleeding_warning_signs | python -m json.tool

# 4c. An invalid trigger — expect 404 listing the valid ones
curl -s localhost:8000/api/guidance/not_a_real_trigger | python -m json.tool
```

**PASS:**
- 4a returns `"status": "covered"` with `entries[]` containing `excerpt`,
  `section` (e.g. `1.8.2`) and a `url`.
- 4b returns `"status": "evidence_gap"`, `"entries": []`, and a populated
  `evidence_note`. **This is correct behaviour, not a bug.**
- 4c returns 404.

---

## Step 5 — Retrieval, no generation (2 min, no API key)

```bash
# Should ANSWER
curl -s -X POST localhost:8000/api/guidance/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"are wrist splints recommended?"}' | python -m json.tool

# Should REFUSE
curl -s -X POST localhost:8000/api/guidance/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"what is the correct dose of alteplase?"}' | python -m json.tool
```

**PASS:**
- First: `"answered": true`, `"mode": "extractive"`, passages cite NICE NG236 1.13.10.
- Second: `"answered": false`, `"mode": "refusal"`, `"passages": []`.

The refusal is the single most important behaviour in the retriever. If it
answers the alteplase question, stop and investigate before demoing.

---

## Step 6 — Generation, the real Claude call (3 min, NEEDS API KEY)

Get a key from <https://console.anthropic.com>. Stop uvicorn, then:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export RECOVERYLENS_LLM_SYNTHESIS=1
pip install anthropic==0.40.0
uvicorn api.main:app --reload --port 8000
```

Re-run the wrist splints query from Step 5.

**PASS:** `"mode": "synthesised"`, and `answer` is a short prose reply citing
`(NICE NG236 1.13.10)` — not a verbatim passage dump.

**Then verify the grounding actually holds.** Ask something the corpus does not
cover but Claude certainly knows from training:

```bash
curl -s -X POST localhost:8000/api/guidance/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"what is the mechanism of action of aspirin?"}' | python -m json.tool
```

**PASS:** `"answered": false`. The model is never reached, because retrieval
declined first. If you ever see a fluent pharmacology answer here, the grounding
has failed and generation must be switched off until it is fixed.

### Troubleshooting

| Symptom | Meaning |
|---|---|
| `"mode": "extractive"` + `[Generation unavailable (ANTHROPIC_API_KEY is not set)...]` | Env var not exported into the uvicorn process |
| `...(anthropic SDK not installed)...` | `pip install anthropic==0.40.0` |
| `...(AuthenticationError)...` | Bad or expired key |
| `...(NotFoundError)...` | Model name rejected; try `export RECOVERYLENS_LLM_MODEL=claude-haiku-4-5-20251001` |

Every one of these degrades to passages rather than erroring — the clinician
still gets the real guidance.

---

## Step 7 — Frontend (5 min)

```bash
cd web
npm install          # only if node_modules is stale
npm run dev
```

Open the dev URL, run an assessment (use a patient with **speech, arm, leg and
visual field deficits** to fire the most triggers).

Check all five:

1. **Guidance section appears** below the timeline — not grey chips.
2. **A card expands** to quoted text with `NICE NG236 1.8.2`-style citations that
   link out to nice.org.uk.
3. **Bleeding warning signs** shows an amber "Evidence gap" panel.
4. **Our words vs theirs are visibly different** — "RecoveryLens note —" in muted
   prose, guideline text in bordered blocks with quotation marks. If these look
   alike, that is a bug worth fixing before submission.
5. **Ask box** answers the splints question and refuses the alteplase one.

No red "Unresolved triggers" banner should ever appear. If it does, the model is
emitting a trigger the corpus cannot resolve.

---

## Step 8 — Citation spot-check (5 min) — DO NOT SKIP

Section numbers were transcribed by hand. Most were read directly off numbered
recommendations, but **four ISA numbers were inferred from document ordering and
are unverified.** Open the ISA PDF and confirm these:

<https://stroke-india.org/wp-content/uploads/2024/07/stroke.pdf>

| Entry id | Claimed section | Check |
|---|---|---|
| `sw_isa_swallow_screen` | 9.0 | Is the swallow-screen text in section 9? |
| `mon_isa_11` | 11.0 | Is "deterioration can occur in 25%" in section 11? |
| `rehab_isa_12`, `rehab_isa_12_referral` | 12.0 | Verified — Stroke Rehabilitation |
| `gen_isa_lifestyle`, `gen_isa_riskfactor_week`, `adh_isa_13_lifestyle` | 13.0 | Verified — Prevention |

Also confirm one inferred NICE number: `arm_ng236_robot` claims **NG236 1.13.18**
for "Do not offer robot-assisted arm training". Its caveat already flags this as
inferred.

Fix any wrong number in `guidance/corpus.json` and re-run Step 1.

A wrong citation is worse than no citation: it looks authoritative and sends a
reader to the wrong place.

---

## Step 9 — Commit (2 min)

```bash
git status                       # confirm no .env, no *.db, no __pycache__
git add guidance/ tests/ docs/ api/ requirements.txt requirements-api.txt web/src/
git commit -m "Add guidance layer: cited corpus, deterministic lookup, clinician RAG"
git push origin main
```

**PASS:** `git status` clean apart from ignored files.

> Never commit `ANTHROPIC_API_KEY`. `.env` is already in `.gitignore` — keep the
> key there or in your shell profile, never in a tracked file.

---

## Still open after all of this

- **NICE copyright permission** — see `GUIDANCE_LICENSING.md`. Short attributed
  quotation is a good-faith posture, not permission. Send the email before you
  deploy publicly.
- **`bleeding_warning_signs`** — decide: source it properly, restrict it to a
  clinician prompt, or retire it. Options are documented in `corpus.json`.
- **IST-3 external validation** — unrelated to this layer, still outstanding.
