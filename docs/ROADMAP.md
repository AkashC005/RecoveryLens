# RecoveryLens — build roadmap

Locked 2026-08-02. Five features, in this order. Total **47–67 focused hours**,
roughly three weeks part-time.

Two facts that shape everything below:

- **IST-1 and IST-3 have entirely different schemas.** IST-1 uses `RDELAY`,
  `RCONSC`, `RSBP`, `RDEF1-8`; IST-3 uses `randdelay`, `gcs_score_rand`,
  `sbprand`, `weakarm_rand`. The feature mapping is the bulk of feature 1, not
  the modelling.
- **The caregiver check-in form does not exist yet.** Only the assessment form is
  built. Features 4 and 5 both depend on it, so it is built inside feature 3.

---

## Twilio sandbox — five minutes, no queue

An earlier version of this file said the sandbox needs approval that "can take
days". **That was wrong.** The WhatsApp Sandbox is self-service and instant: sign
up, click Confirm on the Try WhatsApp page, send `join <your code>` from your
phone. Done.

What genuinely takes days is a **production WhatsApp sender**, which needs Meta
business verification. You do not need that for a demo.

So it is not a blocker and should not displace starting feature 1. Do it whenever
convenient before feature 4.

Three sandbox constraints to know before demo day:

- Every recipient must send `join <code>` before you can message them. If a judge
  wants it on their own phone, they must opt in first — have the QR ready.
- The number is shared Twilio infrastructure, not branded. Say it is a sandbox
  rather than let anyone assume production.
- WhatsApp's 24-hour session window applies: outside it you can only send
  pre-approved templates. For a system that messages someone on day 42, this is a
  real design constraint you will hit in feature 4.

---

## Order

| # | Feature | Hours | Blocked by |
|---|---|---|---|
| 1 | IST-3 external validation | 10–14 | — |
| 2 | Conformal prediction | 5–7 | 1 (uses IST-3 as external calibration) |
| 3 | Escalation triage agent | 10–14 | — (builds the check-in form) |
| 4 | WhatsApp/SMS check-ins | 8–12 | 3 |
| 5 | Multilingual voice guidance | 14–20 | 3 and 4 |

**Critical path: 1 → 3 → 4 → 5.** Feature 2 is additive and can slip without
breaking anything downstream.

---

## 1. IST-3 external validation — 10–14h

The least exciting item and the highest-regret omission. "Did you validate
externally?" is the first question a clinical judge asks, and today the answer is
no. Everything else in this roadmap is decoration on a foundation that one
question can knock over.

- `ml/data/IST3_clean.csv` is already on disk
- Write an explicit IST-1 → IST-3 column mapping as a dict, not inline
- Re-derive the six outcomes from IST-3 columns (`dead7`, `ohs6`, `sich7`,
  `recinfus`) matching the IST-1 definitions exactly
- **Never refit on IST-3.** Load `models/final_*.pkl`, predict, evaluate.
  Refitting turns external validation into a second training run
- Report AUC + CI, calibration slope, CITL, decision curves — the same metrics as
  `outputs/final_metrics.json`, so the two tables sit side by side
- **Expect degradation and report it.** IST-3 is a thrombolysis trial from
  2000–2011; IST-1 is aspirin/heparin from 1991–96. The drop *is* the finding.
  Hiding it would be worse than not doing the validation

## 2. Conformal prediction — 5–7h

- Split-conformal on a held-out calibration set; `outputs/split_indices.csv`
  already exists
- One predictor per outcome. `mapie`, or ~80 lines by hand
- Surface as `probability_lower` / `probability_upper` in the API
- Target 90% coverage, verified empirically on the test set
- Payoff: `recurrence_14d` (AUC 0.59) stops being an apology and becomes an
  honest wide interval

## 3. Escalation triage agent — 10–14h

Reads the `free_text` field in `api/schemas.py:335` — currently the only mention
of it in the entire codebase. A carer's typed concern is stored and never read.

- **Build the caregiver check-in form** (new React route): the three booleans
  plus a free-text box
- Agent tools: `get_patient_risk_profile`, `get_checkin_history`,
  `search_guidance`, `flag_for_clinician`
- **Monotonic escalation, enforced in code and not in the prompt.** The existing
  boolean rules always run first. The agent may only add to that set, never
  remove from it. Worst failure is a false alarm, not a missed deterioration
- Clinician inbox showing the agent's reasoning and which tools it called
- Tests: the agent cannot clear an escalation the rules raised; tool failure
  degrades to rules-only

## 4. WhatsApp/SMS check-ins — 8–12h

`twilio` and `apscheduler` are already pinned in `requirements.txt`.

- Twilio account + WhatsApp sandbox (applied for on day one)
- Public webhook URL — ngrok locally, Render in production
- Scheduler job polling the existing `/api/checkins/due`
- Inbound webhook → parse reply → POST `/api/checkins/{id}/respond`
- **Consent gate**: `Patient.consent_recorded` already exists. Enforce it before
  any message is sent
- Rate limit and a STOP keyword. Non-negotiable for anything messaging a
  patient's family

## 5. Multilingual voice guidance — 14–20h

`Noto Sans Tamil` is already loaded in `web/src/index.css`.

Voice is not decoration here. Text assumes literacy in the target script, which
is exactly the assumption that fails for an elderly or low-literacy carer in a
rural district.

**Output** — the generated caregiver message, translated, delivered as audio.
**Input** — carer speaks the reply; transcription feeds the agent from feature 3.

- Pick two languages. Tamil and Hindi, given the font is already there
- TTS: Google Cloud TTS or Azure Speech. Twilio's built-in TTS if you go the
  phone-call route
- STT: Whisper API or Google STT. **Do not self-host Whisper** — it will not fit
  Render's 512MB tier
- **Read-back confirmation before any action.** The system repeats what it heard
  and asks the carer to confirm
- Transcription confidence threshold; below it, escalate to a human rather than
  guess. Same monotonic rule: uncertainty raises urgency, never lowers it
- **Store the audio as well as the transcript.** A clinician reviewing an
  escalation must be able to hear what was actually said
- Translate the *generated caregiver messages*, never the quoted guideline
  excerpts. A translated NICE recommendation is no longer a quotation
- Back-translation check on text before it is spoken; flag drift for review
- Clinician view stays in English throughout

**The risk that makes read-back mandatory:** dropped negation. *"He can't move
his arm"* transcribed as *"He can move his arm"* inverts the clinical meaning,
and ASR drops short function words most readily. Code-switching makes it worse —
Indian carers routinely mix English medical terms into Tamil or Hindi, which is
where accuracy falls off hardest.

---

## Decisions still open

- **Feature 5 delivery: phone call or WhatsApp voice note.** A call reaches more
  people and demos better; WhatsApp is less engineering and reuses the feature 4
  webhook. Pick one and build it properly rather than half of each.
- **NICE copyright permission** — see `GUIDANCE_LICENSING.md`. Send before any
  public deployment.
- **`bleeding_warning_signs`** — source it, restrict it to a clinician prompt, or
  retire it. Options are documented in `guidance/corpus.json`.

## Presentation arc

Validated externally → predicts → explains → retrieves cited guidance → acts
through an agent → reaches the carer by voice, in their language.

Each step answers "so what?" for the one before it.
