# RecoveryLens — demo run

Two parts. **Part 1** is the full pre-flight: every feature, in order, with what
"working" looks like so you can tell a pass from a near-miss. Run it once
tonight and once in the morning. **Part 2** is the 2½ minutes you actually
perform.

Do Part 1 before you trust anything. Several of these paths have never been
clicked by a human.

---

## Part 0 — Before you start (10 minutes, once)

### 0.1 Build the frontend

```bash
cd ~/Desktop/Project/"Recovery Lens"/web
npm install && npm run build
cd ..
```

### 0.2 Generate the synthetic prescriptions

```bash
source .venv/bin/activate
python -m prescription.testdata.make_synthetic
```

Four PDFs land in `prescription/testdata/`. **Use these, never the real clinic
printout** — it carries a patient's name, hospital number and a surgeon's
registration number, and the finale guidelines forbid showing identifiable
patient data.

### 0.3 Turn the background scheduler OFF

Your `.env` currently has `RECOVERYLENS_SCHEDULER=1` — **twice**. That means a
background job sends check-ins on its own timer, to whatever numbers are in the
database. Mid-pitch, that is a WhatsApp message you did not ask for going to a
real phone at a moment you did not choose.

Open `.env`, delete the duplicate, and set the remaining one:

```
RECOVERYLENS_SCHEDULER=0
```

You lose nothing. Every send in this demo is a button you press.

### 0.4 Clear the database

```bash
python scripts/reset_demo.py
```

Start the demo with nothing in it. An empty patient list is a better opening
than one full of yesterday's test rows called "asdf".

### 0.5 Have ready

- Your phone, WhatsApp open, joined to the Twilio sandbox
- A browser window at 100% zoom, bookmarks bar hidden
- A second tab you can paste the carer link into
- The recorded backup video on this laptop

---

## Part 1 — Full pre-flight (~25 minutes)

Tick each ✅. If one fails, that feature does not go in the pitch.

### 1. Launch

Double-click **`RecoveryLens.app`**.

✅ A Terminal window opens, prints the model and corpus counts, then
`Ready at http://localhost:8000`, and your browser opens.

You should see the split sign-in screen: hero on the left with 19,435 / 0.802 /
IST-3, form on the right.

> **Fails?** The window will name the reason — no virtualenv, no frontend build,
> or port 8000 busy. Free the port with `lsof -ti:8000 | xargs kill`.

### 2. Create an account

Click **Create an account**. Organisation `Stroke Unit`, an email, a password of
at least 12 characters.

✅ You land on **New assessment** with your organisation name top right.

### 3. Invite a colleague

Click your organisation name in the header.

✅ A panel opens, states plainly that no email is sent and that you are choosing
their password. Create one; it confirms *"… can now sign in."*

### 4. Autofill from a discharge summary

On **New assessment**, the top panel reads *Start from a discharge summary*.
Paste a summary into **Paste the discharge summary here…** and click
**Read this summary**.

✅ Fields populate, marked as machine-filled, with an amber banner asking you to
check them.
✅ **Show what it read** reveals the verbatim sentence behind each value.
✅ If anything was refused, it appears under *"It tried and I rejected it"* —
that is the evidence-span check working, not a failure.

**This is the moment to land in the pitch.** Every value carries the sentence it
came from, and any value whose quote is not in the document is rejected rather
than shown.

### 5. Submit the assessment

Add a patient reference — **a ward code, never a name** — and a caregiver
contact (your own phone, `+91…`). Language: Tamil if you want to show
translation. Submit.

✅ Six ranked outcomes with animated percentile bars, tiers and drivers.
✅ A follow-up timeline with check-ins on days 3, 7, 14, 30, 42, 90, 180.
✅ Each date is labelled **Guideline-backed** or **Our scheduling** — never
blurred.
✅ Guidance blocks quote guideline text with section numbers and sources.

### 6. Ask the guidance corpus — and get refused

Scroll to **Ask the guidance corpus**.

Ask something in scope: *"When should antiplatelet therapy start after an
ischaemic stroke?"*
✅ An answer built only from retrieved passages, with the passages shown.

Now ask something out of scope: *"How do I manage a myocardial infarction?"*
✅ **It declines.** It does not answer with stroke guidance.

That refusal is the single most persuasive thing in the whole demo. Before the
thresholds were measured, it answered that question. Show it deliberately.

### 7. Patient record

**Patients** → click the row.

✅ Follow-up delivery banner first: contact on file (last four digits only),
consent recorded, WhatsApp 24-hour window.
✅ Risk profile, follow-up trail, and the collapsed assessment inputs.

### 8. Prescription capture *(built last night — test it properly)*

On the patient record, find **Prescription** → **Upload a prescription** →
`prescription/testdata/prescription_basic.pdf`.

✅ A teal banner: *"Measured from the PDF's column positions."*
✅ Three medicines, and the doses are in the right columns —
CEFVIL shows **1 morning, 1 night**, with **Noon and Evening empty**.
✅ Next visit fills in as 21 Sep 2026.

Now try `prescription_awkward.pdf`.
✅ DEFLAZACORT keeps its **0.5** doses.
✅ DOLO is marked as-needed, and the summary says it will get **no reminder**.

Click **I have checked these against the paper**.

✅ Confirms with your name, and schedules one daily check per prescribed day.
✅ Names which medicines were *not* scheduled and why.
✅ The confirmed list appears under **Confirmed medicines**, tagged *from a PDF*.

> If any of this misbehaves, **leave it out of the pitch.** Slide 5 does not
> depend on it.

### 9. Send a real WhatsApp message

In the follow-up trail, on any unanswered check-in, click **Send now**.

✅ Your phone buzzes.
✅ The screen shows the exact message that went out, the channel, and — if the
patient's language is Tamil — that it went in Tamil.
✅ The check-in badge changes from *Not sent* to *Awaiting reply*.

Try it on a patient with no consent recorded: ✅ amber *"Not sent — …"*, not a
red error. The system did the right thing and says so.

### 10. The carer's side

On the same check-in, click **Get the carer's link**. Paste it into a second tab.

✅ The check-in page opens with **no sign-in** — a carer has no account.
✅ It shows one patient and one check-in. Nothing else is reachable.

Answer the questions. Then use **Or say it out loud**:

✅ Record a short answer.
✅ The transcript is **read back** and must be confirmed before it counts.
✅ Reject the read-back and it is discarded, not recorded.

Add free text: *"He's been more confused since Tuesday and hasn't wanted to eat
much."* → **Send check-in**.

✅ A confirmation that never says the patient is fine — it confirms receipt and
says what happens next.

### 11. Triage and escalation

Back in the clinician window, **Review**.

✅ The check-in appears, escalated, with an urgency chip.
✅ Expand **How this was triaged**: rule reasons and agent reasons shown
separately, with the tool calls the agent made.

Escalation is monotonic and enforced in code — the model can raise urgency and
has no path to lower it.

### 12. Re-assess

Patient record → **Re-assess** next to the patient reference.

✅ The form opens pre-filled, with a banner naming the patient id.
✅ Change one field, submit, and a second assessment appears on the record —
the first is not replaced.

### 13. Opt-out and override

From your phone, reply **STOP** to the WhatsApp message.

✅ The patient record shows *Opted out*, and the banner turns red.
✅ **Send now** is refused.

Click **This opt-out was a mistake**.

✅ It demands a typed reason of at least 15 characters.
✅ After clearing, the record permanently reads *"opted out on … overridden by
you on …"* with your reason — it never reverts to looking like someone who never
objected.

If you show one thing on responsible AI, show this.

### 14. Close

Close the Terminal window.

✅ The server stops. Reloading the browser fails.

---

## Part 2 — The 2½ minutes you perform

Rehearse until you can do it without reading. Timings are from the start of
slide 4.

| Time | Do | Say |
|---|---|---|
| 0:00 | Slide 4 up | *"Everything you're about to see is running, not mocked. Synthetic patient, no real identifiers."* |
| 0:10 | Paste summary → **Read this summary** | *"A discharge summary goes in…"* |
| 0:30 | **Show what it read**, point at one quote | *"…and every field carries the sentence it came from. Any value whose quote isn't in the document is rejected, not shown."* |
| 0:50 | Submit | — |
| 1:00 | Point at the bars | *"Six outcomes, ranked. A percentile against the trial cohort, not a probability — these models rank, they don't calibrate."* |
| 1:20 | Point at a **Guideline-backed** chip | *"Every follow-up date says whether a guideline backs it or whether it's our choice."* |
| 1:35 | Ask the out-of-scope question | *"And when it doesn't know, it says so."* |
| 1:50 | **Send now**, hold up phone | *"That's a real WhatsApp message, in Tamil, to a real phone."* |
| 2:10 | Carer tab → answer badly → send | — |
| 2:25 | **Review** → escalation + trace | *"Triaged, escalated, with the reasoning attached."* |
| 2:30 | Stop. Slide 5. | — |

**Cut in this order if you're over time:** the escalation trace, then the carer
tab, then the guidance question. Never cut the autofill or the refusal — those
are the two moments that separate this from a form with a chart on it.

---

## If it breaks

Do not debug on stage. One sentence — *"I've got a recording"* — then the
backup slide. Rehearse that transition as carefully as the demo.

**The three things most likely to go wrong, and what to do:**

| Symptom | Cause | Do |
|---|---|---|
| Blank page on launch | Frontend not built | `cd web && npm run build`, relaunch |
| Autofill returns nothing | API key rejected | Check `/api/triage/status` — the form still works by hand |
| WhatsApp never arrives | Sandbox session expired | Re-join the Twilio sandbox from your phone; carer link still works |

Check **Review → AI status** before you present: it tells you which model-backed
features are actually live. A silently disabled agent looks exactly like one
that found nothing.
