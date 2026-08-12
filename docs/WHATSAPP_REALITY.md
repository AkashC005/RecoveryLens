# WhatsApp: what works, what cannot, and why

One page, for the question *"can this actually message a real family?"*

The short answer is **yes, and it has** — verified live, end to end. The longer
answer involves two constraints that no amount of engineering removes, and both
are better stated by us than discovered by someone else.

---

## What has actually run

```
check-in sent to WhatsApp
  -> carer replies in plain English
    -> webhook (200 OK, 0.02s)
      -> triage agent reads the free text
        -> urgent escalation -> clinician inbox
          -> confirmation back to the carer
```

Verified with the message *"he's been more confused since Tuesday"*. All three
check-in booleans were unset, so the rule checks alone would have closed it as
"nothing new". The agent escalated it as **urgent**, and its recorded reasoning
cited the check-in history and the risk profile:

> a new, sudden-onset change in cognition with no prior baseline confusion in
> check-in history; combined with this patient's moderate recurrent-stroke risk
> and vigilance flags for haemorrhage/PE, this warrants urgent clinical review

That is the single most useful thing this system does, and it happened over real
Twilio infrastructure to a real phone.

---

## Constraint 1 — the sandbox needs an opt-in

Right now the sender is Twilio's **shared WhatsApp sandbox**. Any number that
wants to receive messages must first send `join <code>` to it.

**Why:** it is a shared test number. Twilio has no way to know we are authorised
to message a stranger, so it requires the recipient to say so first.

**For a demo:** this takes a judge about five seconds if they want messages on
their own phone. It is not a limitation worth hiding — it is opt-in working.

**For production** you need a WhatsApp Business sender: your own number
registered to Twilio, a Meta Business Manager account, Facebook Business
Verification (business documents, days to weeks), and approved message templates.

Two gates found the hard way, both now documented in `TWILIO_SETUP.md`:

| Error | Cause |
|---|---|
| `[572002]` | Trial accounts may only message **Verified Caller IDs** |
| `[20003]` | Paid accounts need an approved **Trust Hub** compliance profile |

Upgrading swaps one for the other rather than removing it.

---

## Constraint 2 — the 24-hour window, which survives everything

This is the one that matters, and it is **Meta policy, not a Twilio limitation**.

Free-form messages may only be sent within **24 hours** of the recipient's last
message to you. Outside that window Twilio returns:

```
[21654] ContentSid Required
```

A pre-approved template is mandatory.

**Every scheduled check-in past day 1 is outside the window by definition.** The
carer has not messaged us — which is precisely why we are messaging them. So
production needs Meta-approved templates for the outbound prompt. Once the carer
replies, a window opens and everything downstream works free-form exactly as
built and already verified.

**An earlier version of our own docs claimed the sandbox does not enforce this.
Testing against the live API disproved that.** It is recorded here because a
document that only lists what worked is not a document anyone should trust.

---

## "So just message any number"

Three reasons that is the wrong ask, in increasing order of importance.

1. **It is not buildable today.** Business Verification takes days to weeks and
   needs company documents.
2. **It would still not work.** Even fully verified, the first message to a number
   that has never written to you *must* be an approved template. Meta does not
   provide an exception, and neither does SMS in India, where DLT registration
   applies.
3. **Our own code would refuse it, and should.** `messaging/policy.py::may_send()`
   gates every outbound message on opt-out, then consent, then a usable number,
   then a rate limit. Messaging someone who never consented is exactly what that
   gate exists to prevent.

For a platform sending clinical follow-ups to the families of stroke patients,
that gate is a feature to point at, not an obstacle to route around. The
`Follow-up delivery` panel on the patient record reads its answer from the same
`may_send()` call the sender uses, so a clinician can see when follow-up has
silently stopped and why.

---

## The slide

> **WhatsApp delivery is live and verified end to end.** A carer's plain-English
> reply reaches the triage agent, which escalated *"he's been more confused since
> Tuesday"* as urgent when the checkbox rules would have closed it.
>
> Two constraints are Meta's, not ours. Recipients must opt in to the shared
> sandbox — five seconds, and it is consent working as intended. And the first
> message to any number must use a pre-approved template, because free-form
> messages are only permitted within 24 hours of the carer's last reply. Every
> scheduled check-in is outside that window by definition.
>
> Production therefore needs Meta Business verification and one approved template
> for the outbound prompt. The consent gate that enforces opt-in is already built,
> already tested, and already visible in the clinician UI.

Understanding the regulatory surface is the point. A demo that texted strangers
would show the opposite.

---

## Rate limits and volume

Twilio's paid tier includes 1,000 free WhatsApp conversations per month; a
conversation is a 24-hour session, not a message. At six check-ins per patient
over six months, message volume is not a constraint worth designing around at
this stage.

`messaging/policy.py` caps sends at **one per patient per 12 hours** and
`messaging/scheduler.py` at **25 per run**, neither of which the real schedule
approaches. They exist so that a scheduler bug cannot turn into dozens of
messages to a worried family — the failure that would end this product's
credibility fastest.
