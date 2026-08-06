# Twilio WhatsApp setup

About 20 minutes. No approval queue — the sandbox is self-service and instant.
What takes days is a *production* WhatsApp sender, which needs Meta business
verification and which you do not need for a demo.

Nothing below is required to develop. Without it, `ConsoleSender` prints
check-ins to the server log and the whole flow works.

---

## 1. Install the SDK (1 min)

```bash
source .venv/bin/activate
pip install twilio==9.3.7
```

---

## 2. Twilio account and sandbox (5 min)

1. Sign up at <https://www.twilio.com/trcly-twilio>. The trial credit is enough —
   WhatsApp sandbox messages cost a fraction of a cent.
2. Console → **Messaging → Try it out → Send a WhatsApp message**.
3. Accept the terms. You are shown a sandbox number (usually
   `+1 415 523 8886`) and a join code like `join amber-tiger`.
4. **From your own phone**, WhatsApp that exact join phrase to the sandbox
   number. You should get a confirmation back within seconds.

**PASS:** Twilio replies confirming you have joined.

> Every recipient must send the join phrase before you can message them. If a
> judge wants it on their phone during a demo, they have to join first — have
> the QR code on a slide.

---

## 3. Credentials into .env (2 min)

From the Console dashboard, copy **Account SID** and **Auth Token**.

```bash
RECOVERYLENS_MESSAGING=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_FROM=whatsapp:+14155238886
```

**The `whatsapp:` prefix on `TWILIO_FROM` is not optional.** Twilio treats
`whatsapp:+14155238886` and `+14155238886` as different channels entirely. Omit
it and sends fail with an error that does not mention the prefix. This is the
single commonest first-time mistake.

Restart uvicorn, then:

```bash
curl -s localhost:8000/api/checkins/1/send | python3 -m json.tool
```

**PASS:** `"channel": "twilio"` and the message arrives on your phone.
If it says `"channel": "console"`, `RECOVERYLENS_MESSAGING` is not set to
`twilio`, or one of the three credentials is missing — the server log says which.

---

## 3b. Trust Hub compliance profile (5 min) — REQUIRED on paid accounts

Not in the original version of this guide, and it blocked every send until it
was done.

On a **paid** account Twilio refuses to send until a compliance profile is
approved, returning:

    [20003] Primary compliance profile is not approved. Please refer to
            documentation and complete the KYC process in Trust Hub.

Note that 20003 is also the code for genuine authentication failure, so the
number alone is misleading — read the message text.

Fix:

1. <https://console.twilio.com/us1/account/trust-hub/customer-profiles>
2. Create a **Primary Customer Profile**, business type **Individual /
   Sole Proprietor** — much lighter than the corporate path.
3. Name, address, email. Approval for an individual profile can be near-instant.
4. Wait a few minutes after approval for the messaging service to pick it up,
   then retry.

The trial account does not require this, but it does restrict recipients to
verified caller IDs (error 572002). So both tiers have a gate; upgrading swaps
one for the other rather than removing it.

---

## 4. Outbound works. Now inbound (10 min)

Replies need Twilio to reach your laptop, which means a public URL.

```bash
brew install ngrok        # or download from ngrok.com
ngrok config add-authtoken YOUR_TOKEN    # free account required
ngrok http 8000
```

Copy the `https://` forwarding URL. Then in the Twilio console, on the same
WhatsApp sandbox page, find **"When a message comes in"** and set it to:

```
https://YOUR-ID.ngrok-free.app/api/webhooks/twilio
```

Method **POST**. Save.

Add the same URL to `.env` — exactly as entered in the console:

```bash
TWILIO_WEBHOOK_URL=https://YOUR-ID.ngrok-free.app/api/webhooks/twilio
```

Signature validation hashes the URL. Behind a tunnel, the URL Starlette
reconstructs is not always the one Twilio signed, so a mismatch here rejects
genuine requests with a 403. Setting it explicitly avoids that.

Restart uvicorn.

**PASS:** reply to the check-in on your phone. Within a second or two you should
get a confirmation back, and the check-in should appear in the **Review** tab if
it escalated.

> The free ngrok URL changes every restart. Update both the console and `.env`
> each time, or the webhook silently stops working.

---

## 5. What to test

| Send this from your phone | Expect |
|---|---|
| `yes no no` | Recorded, no escalation |
| `he's been more confused since Tuesday` | Escalates — the triage agent read it |
| `no change, all fine` | Recorded, no escalation |
| `hmm not sure` | **Escalates** — ambiguity defaults to concerning |
| `STOP` | Opt-out confirmed; further sends refuse permanently |

After `STOP`, `POST /api/checkins/{id}/send` returns
`sent: false, "Recipient has opted out"`. That is permanent by design — only a
human should clear `Patient.opted_out`.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `"channel": "console"` | `RECOVERYLENS_MESSAGING` not `twilio`, or a credential missing |
| Send fails, unhelpful error | Missing `whatsapp:` prefix on `TWILIO_FROM` |
| `sent: false, "No recorded consent"` | `Patient.consent_recorded` is false — set a `caregiver_contact` at assessment |
| `sent: false, "Rate limited"` | One message per patient per 12h. Working as intended |
| Reply arrives, nothing happens | Webhook URL wrong, ngrok restarted, or 403 — check the ngrok request log |
| 403 on the webhook | `TWILIO_WEBHOOK_URL` does not match the console exactly |
| Nothing at all after 24h idle | See below |

---

## The 24-hour window

WhatsApp only allows free-form messages within 24 hours of the recipient's last
message. Outside that, you may only send **pre-approved templates**.

This matters for RecoveryLens specifically: a check-in scheduled for day 42 is
far outside any session window. In production those sends need approved
templates, which takes Meta review.

**CORRECTION — an earlier version of this file said "the sandbox does not
enforce this". That was wrong, and testing against the live API disproved it.**

The sandbox enforces it strictly. Attempting a free-form send outside the window
returns:

    TwilioRestException [21654] Unable to create record: ContentSid Required

`ContentSid` is the ID of a pre-approved template. Twilio is saying: you may only
send a template here, and you have not supplied one.

This is better news than the original claim, not worse — the constraint shows up
in development rather than lying in wait for production. But it means a day-42
check-in cannot be sent free-form at any point, in any environment.

### What this means for RecoveryLens

Every scheduled check-in past day 1 is outside the window by definition. The
carer has not messaged you; that is the whole reason you are messaging them.
So real deployment needs Meta-approved templates for the outbound prompt, after
which the carer's reply opens a 24-hour window and everything downstream —
triage, voice, the agent — works free-form as built.

`messaging/sender.py::within_session_window()` records the constraint; nothing
currently acts on it. Implementing templates means adding a `ContentSid` and
`ContentVariables` to the send call and getting each template approved by Meta.

### Demonstrating it anyway

Send yourself a WhatsApp from the recipient's phone first, which opens the
window, then send the check-in within 24 hours. Everything works. Say plainly
that production would use a template — a limitation you verified against the
live API rather than assumed is a stronger slide than one you did not find.
