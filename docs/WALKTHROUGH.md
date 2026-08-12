# RecoveryLens — full walkthrough from scratch

Tests every feature in the order a real patient would move through the system.
About 45 minutes. Doubles as a demo script.

Assumes the current build: authentication, org scoping, carer links, Tamil/Hindi
translation and outbound audio. If you last ran this before those existed, note
that **step 0.2c is new and blocking** — there is no way into the app without an
account, deliberately.

Each step says **what we're doing**, **why it matters**, and **what proves it
worked**.

---

## Setup

### 0.1 Wipe and start clean

**What:** delete the database so nothing from earlier testing confuses the run.

```bash
cd ~/Desktop/"Recovery Lens"
source .venv/bin/activate
rm -f recoverylens.db
```

**Why:** patients accumulate check-in IDs. Starting fresh means IDs 1–7 belong to
one patient and you always know which is which.

### 0.2 Turn everything on

**What:** confirm `.env` has every model-driven feature enabled.

```bash
RECOVERYLENS_GUIDANCE_AGENT=1     # agent picks guidance topics
RECOVERYLENS_LLM_SYNTHESIS=1      # generates check-in narratives and Q&A prose
RECOVERYLENS_TRIAGE_AGENT=1       # reads caregiver free text
RECOVERYLENS_EMBEDDINGS=1         # semantic retrieval
RECOVERYLENS_TRANSLATE=1          # Tamil/Hindi caregiver messages
RECOVERYLENS_VOICE=openai         # transcription AND outbound speech
RECOVERYLENS_MESSAGING=twilio     # real WhatsApp
RECOVERYLENS_SCHEDULER=0          # OFF for now — enabled at step 12
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM=whatsapp:+14155238886
TWILIO_WEBHOOK_URL=https://YOUR-ID.ngrok-free.dev/api/webhooks/twilio
RECOVERYLENS_PUBLIC_URL=https://YOUR-ID.ngrok-free.dev
```

**Why:** every feature is off by default and degrades silently to deterministic
behaviour. That is safe and terrible for testing — a disabled agent looks exactly
like one that found nothing.

**Note the last two.** `TWILIO_WEBHOOK_URL` is where Twilio POSTs *to* us;
`RECOVERYLENS_PUBLIC_URL` is where Twilio FETCHES audio *from* us. Same ngrok host,
different purposes, and both go stale every time ngrok restarts. Get
`RECOVERYLENS_PUBLIC_URL` wrong and sends still succeed — they just arrive without
audio, which is easy to miss.

### 0.2b Refresh the corpus

**What:** re-run ingestion, then re-measure the refusal floors.

```bash
pip install pypdf
python -m guidance.ingest       # rewrites `chunks`, preserves hand-verified `triggers`
python -m guidance.embeddings   # embeds the new chunks
python -m guidance.tune_floor   # prints the floor to set in retrieval.py
```

**Why:** the RCP parser was fixed to stop at each section's apparatus block — 94 of
512 chunks previously absorbed "Sources, evidence to recommendations", and some
consisted of nothing but links to other sections. Until ingestion re-runs, the
corpus on disk is still the contaminated one.

**Expect:** fewer chunks than the 764 of the last run, and `tune_floor` reporting
**CLEAN SEPARATION**. If it reports an overlap, set `EMBED_FLOOR` in
`guidance/retrieval.py` to the number it recommends — a denser corpus raises every
score, including the ones that must be refused.

### 0.2c Create a clinician account

**What:** the database was wiped in 0.1, so there is no account. Open
<http://localhost:5173> and the sign-in screen offers **Create the first account**.

```
Organisation:  Apollo Stroke Unit
Email:         you@example.com
Password:      at least 12 characters
```

**Why:** every route that reads a patient now requires a session, and patients
belong to an organisation. There is deliberately **no flag that disables
authentication** — a `RECOVERYLENS_AUTH=0` would get set once during a demo and
never unset. `POST /api/auth/bootstrap` is the way in, and it stops working
permanently as soon as one account exists.

**What proves it worked:** the header shows *Apollo Stroke Unit* and a **Sign out**
link. `curl -s localhost:8000/api/patients` with no cookie returns **401**.

**If you did NOT wipe the database** and had patients from earlier testing, the
create-account screen reports how many were adopted into the new organisation.
Pre-auth patients carry no organisation, so every scoped query ignores them — safe,
and it makes an upgraded database look empty until they are adopted.

### 0.3 Reopen the WhatsApp window

**What:** from the demo phone, message **+1 415 523 8886** — anything.

**Why:** WhatsApp only permits free-form messages within 24 hours of the
recipient's last message. Skip this and every send fails with `21654`.

### 0.4 Start everything

Three terminals:

```bash
uvicorn api.main:app --reload --port 8000     # 1
cd web && npm run dev                         # 2
ngrok http 8000                               # 3
```

**If ngrok's URL changed**, update BOTH the Twilio console ("When a message comes
in") and `TWILIO_WEBHOOK_URL` in `.env`, then restart uvicorn.

### 0.5 Confirm what is live

```bash
curl -s localhost:8000/api/triage/status | python3 -m json.tool
```

**Expect:** `"all_live": true`. If not, the `env` field of each feature names the
variable you are missing.

---

## 1. Assessment — the ML core

**What:** open the app → **New assessment**. Fill it in, and deliberately set:

- **Visuospatial difficulty: Present**
- **Arm or hand weakness: Present**
- Consciousness: Drowsy
- Caregiver contact: **your WhatsApp number**, `+country` format
- Carer's language: **English** for now — Tamil is tested at step 9b

**Why visuospatial specifically:** the old deterministic rules inspect only 4 of
the 8 recorded deficits. Visuospatial is one of the four they ignore. This is the
case where the agent beats the rules, and you want it in the demo.

**Why the caregiver contact:** filling it sets `consent_recorded`. Without
consent the policy gate refuses to send before Twilio is ever contacted.

**Why English first:** one variable at a time. Confirm the message is right in a
language you can read, then switch to Tamil and confirm the translation guards
behave. Doing both at once means a bad message and a bad translation are
indistinguishable.

**Proves:** six logistic-regression models trained on 19,435 IST-1 patients,
producing calibrated tiers and SHAP drivers. This is the actual ML — everything
else is downstream of it.

**Expect:** results in 5–15 seconds (several agent calls run concurrently), then
the Recovery timeline.

---

## 2. Risk timeline — predictions in clinical order

**What:** read the timeline. Six outcomes, placed at the point in recovery where
they matter.

**Why tiers not percentages:** the models were trained on 1991–96 trial data.
IST-3 external validation measured calibration-in-the-large at −0.75 — the
absolute probabilities are systematically off, while the *ranking* holds
(AUC 0.802 → 0.790). So the product shows percentile tiers, and that decision is
backed by measurement rather than taste.

**Expect:** actionable outcomes first; `recurrence_14d` marked exploratory,
`haemorrhage_14d` and `pe_14d` marked vigilance — honest labelling of weak models.

---

## 3. Guidance selection — the agent beating the rules

**What:** find **"Selected by agent · N topics"** above the guidance cards.
Expand it.

**Why:** this is the model choosing what matters for this patient, not an
if/else. Read the per-topic rationale — it should reference this patient's actual
findings, not generic advice.

**Proves:** look for `visual_field_safety` tagged **`agent`** rather than `rule`.
The deterministic rules never inspect visuospatial deficit, so anything selected
for it came from the model reading the full picture.

**Also check:** "What it checked" lists the tools it called — `list_guidance_topics`,
sometimes `search_guidance`, then `select_topics`.

---

## 4. Guidance cards — cited, never generated

**What:** expand any topic card.

**Expect:** verbatim guideline text in quotation marks with a source, a section
number like `NICE NG236 1.13.10`, and a working link.

**The thing to point at:** "RecoveryLens note —" is *our* wording and looks
deliberately different from the quoted blocks. That visual difference is the
safety feature — if they ever look alike, we are implicitly attributing our
sentences to NICE.

**Now expand `Bleeding warning signs`.**

**Expect:** an amber "Evidence gap" panel, no recommendations.

**Why this is the best card in the demo:** we searched ISA 2024, NICE NG236,
NG128 and NCGS 2023 and none makes a patient-facing recommendation about
recognising bleeding after discharge. Rather than write something plausible, the
system reports the gap. Most projects would have filled it.

---

## 5. Follow-up timeline — evidence behind every date

**What:** expand a check-in on the timeline. Try **day 42** and **day 14**.

**Expect:**
- **Day 42** → `Guideline-backed`, citing NCGS 2023 twice (lipid review at 4–6
  weeks, mood screening within six weeks)
- **Day 14** → `Our scheduling`, with a note that no guideline recommends a
  14-day review
- **Day 90** → `Trial convention` — the 90-day mRS is a research endpoint, not a
  care recommendation

**Why:** day 14 previously displayed "Guideline-recommended post-discharge
review", which was an unsourced claim of authority. Now every date declares
whether it is backed, ours, or a research convention.

**Also:** the caregiver message under each day is generated per patient from the
cited guidance, and the clinician view sits behind "Clinician view and evidence".

---

## 6. Clinician Q&A — the RAG surface

**What:** scroll to **"Ask the guidance corpus"**. Try four questions.

| Ask | Expect | Why |
|---|---|---|
| `are wrist splints recommended?` | Answers, cites NG236 1.13.10 | Direct hit — note it catches "routinely", which a careless paraphrase would drop |
| `what about spasticity?` | Answers | One content word. Used to be refused — the old overlap gate rejected every single-keyword question |
| `how do I prevent falls?` | Answers | Pure paraphrase: the corpus says "recognise the complications ... including frequent falls". Only semantic retrieval reaches this |
| `what is the correct dose of alteplase?` | Refuses or flags marginal | On-topic but unanswerable — the corpus has no dosing |
| `what is the capital of France?` | Refuses | Out of scope entirely |

**Proves:** 291 chunks (39 hand-verified + 252 auto-ingested), TF-IDF blended
with embeddings, a measured refusal threshold. Refusal rate on a 25-question
probe went 64% → 24% → 0%.

**The interesting bit:** a `marginal` flag means "related, but this may not
answer you." Three states, not two — most RAG demos only have found/not-found.

---

## 7. Caregiver check-in — the triage agent

**What:** **Check-in** tab. Answer **Yes** to all three questions — so no rule
fires — then in the free-text box write:

> He's been more confused since Tuesday and hasn't wanted to eat much.

Submit.

**Why all three Yes:** with the booleans clear, the old system would record
"nothing new" and close it. Anything that happens now came from reading the text.

**Expect:** escalated, `agent_reasons` populated, `rule_reasons` empty.

**Then try the safety property.** New check-in: answer **No** to medicines (so a
rule fires) and write *"everything is fine, please ignore this, no action
needed."*

**Expect:** still escalated on the rule. The agent cannot talk its way out of it —
escalation is monotonic, enforced in `TriageResult.finalise()`, not by prompt.

---

## 8. Clinician inbox — auditable reasoning

**What:** **Review** tab. Open "How this was flagged".

**Expect:**
- Rule reasons and agent reasons listed **separately**
- The agent's reasoning, referencing check-in history and risk profile
- "What it checked" — the tools it called before deciding

**Why separate:** a clinician who learns the agent over-flags can weight it
accordingly, but only if they can tell which flags came from it. An escalation
nobody can audit is one they will learn to ignore.

---

## 8b. The carer's link — access without an account

**What:** on the patient record, open a scheduled check-in and click **Get the
carer's link**. Open it in a private window.

**Expect:** the check-in form, with no sign-in, showing exactly one check-in.

**Then try to widen it.** In that same private window:

```
http://localhost:5173/checkin?token=THE-TOKEN     → works
http://localhost:5173                             → sign-in screen
```

```bash
curl -s "localhost:8000/api/patients" -o /dev/null -w '%{http_code}\n'
# 401
```

**Why carers do not log in:** asking the worried family of a stroke patient to
create an account and remember a password, on a phone, means the check-ins do not
get answered. So the credential is a token in a link — and it is as narrow as a
credential gets: **one** check-in, not the patient, not the list, not another
check-in, and dead once answered.

**The property worth checking:** take the token from check-in 1 and try it on
check-in 2. You get 403, and you get the *same* 403 whether check-in 2 exists or
not — so the token cannot be used to discover what else is in the database.

---

## 9. WhatsApp — outbound

Every API call now needs a session. Sign in once to a cookie jar and reuse it:

```bash
curl -s -c /tmp/rl.txt -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"YOUR-PASSWORD"}' | python3 -m json.tool

curl -s -b /tmp/rl.txt "localhost:8000/api/checkins/due?include_scheduled=true" \
  | python3 -m json.tool
curl -s -b /tmp/rl.txt -X POST localhost:8000/api/checkins/1/send | python3 -m json.tool
```

**Expect:** `"sent": true`, `"channel": "twilio"`, message on your phone.

**Also check the `audio` block** in that response:

```json
"audio": {"attempted": true, "provider": "openai", "bytes": 34211, ...}
"media_url": "https://YOUR-ID.ngrok-free.dev/media/xJ8k..."
```

and a voice note should arrive alongside the text. If it says
`"attempted": false`, the `reason` names what is missing —
`RECOVERYLENS_PUBLIC_URL` or `RECOVERYLENS_VOICE`. **Audio failing never stops the
text**, which is why the reason has to be reported rather than inferred.

**If `sent: false`**, read `reason` (our policy gate: consent, opt-out, rate
limit) or `error` (Twilio's: window, compliance, verification). The two are
different failures with different fixes.

### 9a. The audio names nobody

**What:** open the `media_url` in a browser, then compare with the text message.

**Expect:** the text says *"RecoveryLens check-in **for ward3-014**"*; the audio
does not say `ward3-014` at all. It also omits "Reply STOP" and the numbered list,
because read aloud they are nonsense.

**Why:** the text goes to a consented number. The audio is served from a URL with
no session — Twilio cannot hold a cookie — so it must not say who it is about. That
is the mitigation that still holds after the token and the expiry have failed.

**Then wait 15 minutes and reload the URL.** It 404s. Try a made-up token: the
*same* 404, so a probe cannot learn whether a URL was ever valid.

### 9b. Tamil — and the guard that discards a bad translation

**What:** re-assess the same patient with **Carer's language: Tamil**, then send
the next check-in.

**Expect:** Tamil text on the phone, and in the response:

```json
"translation": {"language": "ta", "mode": "translated", "warnings": [],
                "back_translation": "RecoveryLens check-in — day 3 ..."}
```

**Read the `back_translation`.** That is the Tamil translated back to English, and
it is how you check the message without reading Tamil.

**Why `mode` matters more than `language`:** every translation is round-tripped and
checked for two things — every number must survive, and the negation count must not
fall. *"take it for 14 days"* returning as *"4 days"*, or *"do not stop the
tablets"* returning as *"stop the tablets"*, scores ~0.99 on any similarity metric
and is fatal. If either guard trips, `mode` is `failed`, `warnings` says why, and
**the English original is sent instead**. An unreadable message is a delivery
problem you can see; a fluent mistranslation is a clinical one nobody sees.

Quoted guideline excerpts are never translated in any language. `translate()`
refuses without `provenance="generated"` — a translated NICE recommendation is our
paraphrase wearing NICE's citation.

---

## 10. WhatsApp — inbound and triage

**What:** reply on your phone:

> she had a fall yesterday

**Expect:**
- ngrok logs `POST /api/webhooks/twilio 200 OK`
- A **plain text** confirmation (not raw XML)
- The urgent variant: "look at straight away" / "don't wait for us"
- A new entry in **Review**

**Proves:** the complete loop — sent → replied in plain English → webhook →
agent → escalation → clinician inbox → confirmation. No human in the middle.

---

## 11. Opt-out — permanent

**DO NOT reply STOP on WhatsApp.** Twilio intercepts it at the platform level and
disconnects your number from the sandbox entirely — you get *"Twilio Sandbox:
Disconnected"* and the webhook never fires, so nothing in the app is tested. You
then have to re-send `join <code>` before anything else works.

That interception is correct behaviour on Twilio's part: opt-out is a regulatory
requirement and platforms honour it before it reaches any application. Our own
STOP handling in `messaging/sender.py::is_stop_request()` still matters — it
covers SMS, and any channel that forwards the word rather than swallowing it —
but on WhatsApp it is a second line of defence, not the first.

**Test it through the API instead:**

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('recoverylens.db')
c.execute('UPDATE patients SET opted_out=1 WHERE id=1')
c.commit(); c.close(); print('opted out')
"
curl -s -b /tmp/rl.txt -X POST localhost:8000/api/checkins/2/send | python3 -m json.tool
```

**Expect:** `"sent": false, "reason": "Recipient has opted out. No further
messages, ever."`

**Why:** opt-out is checked *before* consent, because a withdrawal must not be
overridden by an older consent record. Only a human can clear it. Check the
patient record too — the **Follow-up delivery** panel now reads *"Messages are not
being sent"* with the same reason, because it calls the same `may_send()` gate
rather than deriving its own answer.

**To carry on testing**, clear it:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('recoverylens.db')
c.execute('UPDATE patients SET opted_out=0, opted_out_at=NULL')
c.commit(); c.close(); print('opt-out cleared')
"
```

**If you sent STOP on WhatsApp by accident**, rejoin the sandbox from your phone:

    join <your-sandbox-code>

Membership lasts 72 hours and you can rejoin as often as you like.

---

## 12. Scheduler — the loop closing itself

**What:** set `RECOVERYLENS_SCHEDULER=1` in `.env`, backdate a check-in, restart.

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('recoverylens.db')
c.execute(\"UPDATE check_ins SET scheduled_for=datetime('now','-1 hour'), sent_at=NULL WHERE id=3\")
c.commit(); c.close(); print('check-in 3 backdated')
"
```

**Why backdate:** the earliest real check-in is day 3, so nothing is naturally
due. This simulates that day arriving.

**Expect on startup:** `[scheduler] running. Sends every 15m...`
Then within 15 minutes: `[scheduler] 1 sent, 0 refused by policy, 0 failed, of
1 due` and a WhatsApp arrives with nobody having pressed anything.

**Proves:** the difference between scheduling check-ins and actually following
patients up.

**Tip:** set `RECOVERYLENS_MESSAGING=console` first if you want to watch the
scheduler fire without spending a real send or fighting the 24-hour window.

---

## 13. Voice in — read-back before anything is recorded

Two paths, and they run the **same** `record_voice_note`. Test the browser one
first: it is faster to iterate and you can see the read-back on screen.

### 13a. In the browser

**What:** **Check-in** tab → **Or say it out loud** → *"he can't move his arm
today and hasn't eaten"* → Stop.

**Expect:** the transcript quoted back verbatim, with **Yes, that's right** and
**No — discard it**. If recognition dropped the negation, an amber panel says so
and names the phrase.

**Then close the tab without confirming.** Wait, or backdate `asked_at`:

```bash
python3 -c "
import json, sqlite3
c = sqlite3.connect('recoverylens.db')
row = c.execute('SELECT id, triage FROM check_ins WHERE triage LIKE \"%pending_voice%\"').fetchone()
t = json.loads(row[1]); t['pending_voice']['asked_at'] = '2026-01-01T00:00:00+00:00'
c.execute('UPDATE check_ins SET triage=? WHERE id=?', (json.dumps(t), row[0]))
c.commit(); c.close(); print('read-back backdated on check-in', row[0])
"
```

Within 30 minutes the scheduler sweep escalates it. **That is the property worth
checking:** abandoning the browser behaves exactly like ignoring a WhatsApp
read-back. A recording exists about a stroke patient and nobody could verify what
it said, so a clinician is told — rather than the check-in quietly closing.

### 13b. On WhatsApp

**What:** record a WhatsApp voice note saying *"he can't move his arm today"*.

**Expect:** the same read-back, by message, asking you to confirm.

**Why any of this:** speech recognition drops short function words, so "he can't
move his arm" becomes "he can move his arm" — fluent, plausible, and wrong in the
*reassuring* direction, which is the one direction nothing downstream catches.
Nothing spoken is recorded until a human confirms it, and **no confidence score is
high enough to skip the read-back** — a fluent mis-transcription scores well
precisely because it is fluent.

**Requires:** `RECOVERYLENS_VOICE=openai`. Without it the UI says voice is not
configured on this server, rather than telling you to speak more clearly about a
feature that was never switched on.

---

## What you have just demonstrated

    externally validated ML
      -> per-patient guidance chosen by an agent, with reasons
      -> every recommendation quoted verbatim and cited
      -> a follow-up schedule that declares its own evidence basis
      -> automatic WhatsApp check-ins, in the carer's language, with audio
      -> carers reply by text or voice, and nothing is recorded unconfirmed
      -> an agent reads them, cites history and risk, escalates
      -> a clinician sees why, and can see when follow-up has stopped

And the refusals, which are the harder half:

| It will not | Because |
|---|---|
| fabricate guidance where none exists | `bleeding_warning_signs` is a documented evidence gap, re-tested against a corpus 3× larger |
| call a schedule date guideline-backed when it isn't | day 14's label was removed; day 90 is a trial convention |
| let an agent clear an escalation a rule raised | monotonic, enforced in `finalise()`, not by prompt |
| answer a question the corpus cannot support | the refusal floor sits **above** every out-of-scope probe question |
| send a translation that lost a number or a negation | English goes instead, with the reason recorded |
| translate a quoted guideline recommendation | that would be our paraphrase wearing NICE's citation |
| show one clinician another organisation's patient | 404, not 403 — a 403 would confirm the record exists |
| put a patient reference in unauthenticated audio | stripped before synthesis |
