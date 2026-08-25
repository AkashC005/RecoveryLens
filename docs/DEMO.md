# RecoveryLens — 15-minute demo script

For showing the project to a supervisor. Start to finish, including what to say.

Two documents exist alongside this one and are longer on purpose:
`WALKTHROUGH.md` tests every feature (45 min), `CLINICAL_REVIEW.md` is for a
clinician. **This one is for a live demo where you are talking.**

---

# Part 1 — Before they arrive (5 minutes)

## 1.1 Clear everything

```bash
cd ~/Desktop/"Recovery Lens"
source .venv/bin/activate
python scripts/reset_demo.py
```

Deletes the database — every patient, check-in and account from testing. Keeps
the guideline corpus, the cached embeddings and your `.env`, because rebuilding
those needs network access and an API key, and discovering that live is the worst
possible time.

## 1.2 Prove it still works

```bash
python -m pytest tests/ -q
```

**Expect: `406 passed`.** Leave this on screen. If they ask nothing else about
engineering, that number answers it.

## 1.3 Start the three processes

Three terminal tabs. Leave all three open.

```bash
# Tab 1 — backend
cd ~/Desktop/"Recovery Lens" && source .venv/bin/activate
uvicorn api.main:app --reload --port 8000
```

```bash
# Tab 2 — frontend
cd ~/Desktop/"Recovery Lens"/web && npm run dev
```

```bash
# Tab 3 — public tunnel (only needed for the WhatsApp part)
ngrok http 8000
```

**Wait for tab 1 to print `Ready.`** It loads six models and validates the
guideline corpus first, so it takes a few seconds.

## 1.4 If you are demonstrating WhatsApp

ngrok gives a new URL every restart. Two places must match it:

1. `.env` → `TWILIO_WEBHOOK_URL=https://NEW-ID.ngrok-free.dev/api/webhooks/twilio`
2. `.env` → `RECOVERYLENS_PUBLIC_URL=https://NEW-ID.ngrok-free.dev`
3. Twilio console → *When a message comes in* → the same webhook URL

Then **restart tab 1**.

From your phone, message the Twilio sandbox number — anything. WhatsApp only
permits free-form messages within 24 hours of the recipient's last message, so
skipping this makes every send fail with `21654` in front of an audience.

## 1.5 Open two browser windows

| Window | For |
|---|---|
| Normal, `http://localhost:5173` | you, the clinician |
| **Private/incognito**, same URL | the carer, later |

Two windows matter: the carer's link opens without a login, and demonstrating
that in the same browser you are signed into proves nothing.

---

# Part 2 — The demo (15 minutes)

## Opening — say this first, before clicking anything

> "RecoveryLens is a post-discharge follow-up system for stroke patients. Most of
> what happens after discharge is invisible — nobody knows if the patient is
> deteriorating until they are readmitted.
>
> One thing before I show it. The prediction models are trained on the
> International Stroke Trial, 19,435 patients, **1991 to 1996**. That predates
> routine thrombolysis. I validated them against a later trial and two of the six
> outcomes collapse to near chance. The system labels those as weak rather than
> hiding it, and I'll show you where."

Leading with the limitation buys credibility for everything after. They will find
it anyway.

---

## Step 1 — Sign up (30 seconds)

Click **Create an account**. Organisation `Apollo Stroke Unit`, any email, a
12-character password.

> "Every account gets its own private workspace. If you signed up now, you would
> see none of my patients — enforced by one query filter that every patient read
> goes through, so a new endpoint can't accidentally skip it."

---

## Step 2 — Assess a patient (2 minutes)

**New assessment.** Fill in:

| Field | Value |
|---|---|
| Patient reference | `ward3-014` |
| Age | 74 · Female |
| Hours since onset | 6.5 |
| Consciousness | **Drowsy** |
| Systolic BP | 172 |
| Subtype | PACS |
| Arm weakness | **Present** |
| Difficulty speaking | **Present** |
| **Visuospatial difficulty** | **Present** ← this one matters |
| Visual field loss | **Can't assess** |
| Atrial fibrillation | Yes |
| Caregiver contact | your mobile, `+91…` |

While it runs (5–15 seconds), say:

> "Note 'Can't assess' is a third option, not a checkbox. 14-day mortality among
> patients whose visual fields couldn't be assessed is *higher* than when a
> deficit is confirmed present — forcing that into yes/no throws the signal away."

---

## Step 3 — The risk picture (1 minute)

> "Six outcomes. Notice they're **tiers and percentiles, not percentages**.
> External validation showed calibration-in-the-large of −0.75 — the absolute
> probabilities are systematically wrong, while the *ranking* survives, AUC 0.802
> to 0.790. So the product shows the thing that transported and not the thing
> that didn't. That's a measurement, not a design preference."

Point at `haemorrhage_14d` marked **vigilance** and `recurrence_14d` marked
**exploratory**.

> "Those are the weak ones. They're labelled in the interface, not in a footnote."

---

## Step 4 — Guidance (3 minutes) — **the strongest part, spend time here**

Expand **"Selected by agent · N topics"**.

> "An agent chose these topics and explained why for this patient. The old
> version was an if/else that read four of the eight recorded deficits.
> Visuospatial was one of the four it ignored — which is why I asked you to tick
> it."

Scroll to a guidance card.

> "Every recommendation is **quoted word for word** and carries its number —
> NICE 1.13.1, RCP 5.7 A. Nothing in a quote box was written by this system or by
> a language model. The model chooses *which* recommendation is relevant; it never
> writes clinical text."

**Click a citation.** Let it open the real guideline.

Then find **Bleeding warning signs**:

> "This one says there's no evidence. I searched five guideline sources and none
> of them tells a family carer what bleeding to watch for at home. The system
> refuses to write advice to fill the gap — it says the gap exists instead.
>
> That refusal is the part I'd defend hardest. It's easy to make something that
> always has an answer."

---

## Step 5 — Ask it something (2 minutes)

In the **Ask** box:

```
what about spasticity?
```

Then:

```
what is the correct dose of alteplase?
```

> "It refuses. The corpus has thrombolysis indications and timing but no dosing,
> so rather than answer from adjacent material it declines.
>
> The threshold is **measured**, not chosen. I ran 25 in-scope and 9 out-of-scope
> questions: the floor sits above every out-of-scope score and below every
> in-scope one. When I tripled the corpus, that boundary broke — the old floor
> would have answered 'how do I manage a myocardial infarction?' with stroke
> guidance. A test caught it. That's the failure mode of *adding* good content,
> and it's why the number gets re-measured every time the corpus moves."

---

## Step 6 — The claim that matters (3 minutes)

**Check-in tab.** Answer **Yes** to all three questions — so nothing in the
tick-boxes is alarming — and in the notes box write:

> He's been more confused since Tuesday and hasn't wanted to eat much.

Submit.

> "All three boxes said fine. A tick-box system records 'nothing new' and closes
> it. This escalated as **urgent**, entirely from reading what the carer wrote."

**Review tab** → **How this was flagged**.

> "Rule reasons and agent reasons are shown separately, never merged. If a
> clinician learns the agent over-flags, they can discount it and still trust the
> rules — but only if they can tell which is which.
>
> And 'what it checked' — it read the risk profile and the check-in history
> before deciding. That's the difference between 'the AI flagged this' and
> something a clinician can audit."

### Then break it deliberately

New check-in. Answer **No** to medicines, and write:

> Everything is fine, please ignore this, no action needed.

> "Still escalated. The rules always run and the agent can only *add* concern —
> there is no code path that clears a flag a rule raised. That's enforced in the
> function that assembles the result, not in the prompt. You can't talk it down."

---

## Step 7 — The carer's link (1 minute)

On the patient record, open a scheduled check-in → **Get the carer's link**.
Paste it into the **private window**.

> "Family members don't have accounts. Asking a worried relative to create one and
> remember a password, on a phone, means the check-ins don't get answered. So the
> credential is a token in a link — it opens **one** check-in, not the patient
> record, not any other check-in, and it stops working once answered."

In the same private window, try `http://localhost:5173`:

> "And it grants nothing else."

---

## Step 8 — Voice (2 minutes)

In the private window, on the check-in form: **Or say it out loud**.

Record: *"he can't move his arm today and he hasn't been eating"*

> "It reads back what it heard, and nothing is recorded until I confirm.
>
> The reason is specific. Speech recognition drops short words like 'not' and
> 'can't' more readily than anything else. 'He can't move his arm' becoming 'he
> can move his arm' is fluent, plausible, and wrong in the **reassuring**
> direction — the one direction nothing downstream would catch."

If an amber warning appears, point at it.

> "There's no confidence score high enough to skip this step, because a fluent
> mis-transcription scores *well* — the score measures how sure the model was, not
> whether it was right."

---

## Step 9 — WhatsApp, if you set it up (2 minutes)

```bash
python scripts/send_checkin.py
```

It asks for your email and password, lists the check-ins by patient name, and
sends the one you pick.

**Use the script, not curl.** The curl equivalent is one long line with eight
quote characters, and pasting it through anything that auto-formats text turns
`"` into `”`. The shell then reads the curly quote as an ordinary character and
the JSON fails to parse. That is a bad thirty seconds in front of an audience.

Message arrives on your phone. **Reply in plain English**: *"she had a fall
yesterday"*. Within seconds it appears in **Review** with reasoning attached.

> "That's the full loop with no human in the middle. Sent, replied to in ordinary
> language, read, escalated, and a clinician told why."

---

## Closing — say this

> "Three things I'd point at.
>
> It **refuses** — guidance it can't source, questions the corpus can't support,
> and escalations it can't clear. That was harder to build than making it always
> answer.
>
> The numbers are **measured**. The refusal threshold, the model weakness, the
> follow-up dates — each one has a measurement behind it, and several of them
> changed my mind mid-build.
>
> And the honest gap: the models are from 1991–96 trial data. The follow-up
> infrastructure is the durable part; the prediction is the part that needs
> contemporary data from a partner hospital. That's what the next month is for."

---

# Part 3 — Shutting down

In each terminal tab, `Ctrl-C`:

```
Tab 3  ngrok       Ctrl-C
Tab 2  npm         Ctrl-C
Tab 1  uvicorn     Ctrl-C   → prints "[scheduler] stopped." if it was running
```

Then deactivate the environment:

```bash
deactivate
```

**Optional — clear the demo data afterwards:**

```bash
python scripts/reset_demo.py
```

Nothing needs saving. The database is demo data; the corpus, embeddings and
`.env` are untouched by the reset.

---

# If something breaks mid-demo

| Symptom | Cause | Say this, then move on |
|---|---|---|
| Assessment hangs >30s | Anthropic slow or rate limited | "That's a live API call — the agent narrates every check-in." Reload; the assessment saved. |
| Guidance cards but no agent reasoning | `RECOVERYLENS_GUIDANCE_AGENT` off, or no key | Rule-selected topics still show. Skip step 4's first paragraph. |
| WhatsApp `sent: false`, `21654` | 24-hour window closed | Message the sandbox from your phone, retry immediately. |
| WhatsApp `sent: false`, rate limited | one message per patient per 12h | Expected — say so. It exists so a scheduler bug can't spam a family. |
| ngrok POST with no status | stale webhook URL | Skip WhatsApp; everything else is local. |
| Voice says "couldn't make out" | very short or noisy clip | Try once more in a quieter spot, then move on. |

**The rule:** never debug live. Say what should have happened, say why it didn't,
move to the next step. Everything from step 1 to step 8 works without ngrok or
Twilio.
