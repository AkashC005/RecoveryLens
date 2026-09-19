# Deploying RecoveryLens — first time, step by step

You have a GitHub repo already (`AkashC005/RecoveryLens`) and a working app.
This turns it into a link.

**Allow 45 minutes.** Most of it is waiting for a build. Do it now, not an hour
before you present.

**Before you start, know the one real risk:** on Render's free tier the service
goes to sleep after ~15 minutes idle, and the next request takes roughly 50
seconds to wake it. In a 7-minute pitch that is fatal. Step 8 deals with it.

---

## What we are building

One service. The Docker image builds your frontend, then the Python API serves
both the API *and* those built files. That means **one URL, no CORS to
configure, no second platform**. An earlier config in this repo split it across
Render + Vercel; that is two accounts and two things to break, so we are not
doing it.

---

## Step 1 — Check nothing secret is about to be published (2 min)

Already verified for you, but run it yourself — you are about to make this
public and you should see the output with your own eyes:

```bash
cd ~/Desktop/Project/"Recovery Lens"
git ls-files --error-unmatch .env 2>/dev/null && echo "DANGER: .env is tracked" || echo "OK: .env is not tracked"
git log --all --oneline -- .env | head
```

The first line must say **OK**. The second must print **nothing**.

If either fails, stop and tell me — a key in git history is not fixed by
deleting the file.

---

## Step 2 — Commit everything (5 min)

You have about 30 changed and new files, including last night's prescription
feature and the deployment config.

```bash
git add -A
git status --short | head -40
```

Read that list. Check `.env` is **not** in it.

```bash
git commit -m "Prescription capture, single-process serving, Docker deployment"
git push origin main
```

> If `main` is rejected, your branch may be called `master` — run
> `git branch --show-current` and push that name instead.
>
> If git asks for a password, GitHub no longer accepts them. Create a token at
> **github.com → Settings → Developer settings → Personal access tokens →
> Tokens (classic)**, tick `repo`, and paste the token as the password.

---

## Step 3 — Make a Render account (3 min)

1. Go to **render.com** → **Get Started**
2. **Sign in with GitHub** — this is the easy path, it wires up repo access
3. Authorise Render to see your repositories

No card needed for the free tier.

---

## Step 4 — Generate your signup code (1 min)

**Do not skip this.** Your app has open registration, which is right on a laptop
and wrong on a public URL: without a code, anyone who finds the link can create
an account and start entering data.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

Copy the output somewhere you can find it in 30 seconds. You will need it to
create your own account on the deployed site, and to give to a judge who wants
to look around.

---

## Step 5 — Create the service (5 min)

1. Render dashboard → **New +** → **Blueprint**
2. Pick **RecoveryLens** from your repo list
3. Render reads `render.yaml` and shows a service called **recoverylens**
4. It will prompt for every value marked `sync: false`. Fill in:

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key |
| `OPENAI_API_KEY` | your key (embeddings, voice) |
| `RECOVERYLENS_SIGNUP_CODE` | the string from Step 4 |
| `TWILIO_ACCOUNT_SID` | only if sending real WhatsApp from the deployed copy |
| `TWILIO_AUTH_TOKEN` | same |
| `TWILIO_WHATSAPP_FROM` | same |

Leave the Twilio ones blank if you will demo messaging from your laptop
instead. The app runs fine without them.

5. Click **Apply** / **Create**

These are stored encrypted by Render. This is why none of them are in the file.

---

## Step 6 — Wait for the build (10–15 min)

Watch the log. It is doing three things: building your frontend with Node,
installing Python packages, then checking the startup files exist.

**What a good build ends with:**

```
startup files present
==> Build successful
==> Deploying...
Loaded 6 models. Guidance corpus: ...
Ready.
==> Your service is live 🎉
```

**If it fails**, the message tells you which stage:

| Log says | Means | Fix |
|---|---|---|
| `missing from the image: models/...` | a needed file was not committed | `git add -f models/ && git commit && git push` |
| `npm ERR!` | frontend build broke | run `cd web && npm run build` locally, fix, push |
| `No matching distribution` | a pinned package has no wheel | tell me which one |
| Build fine, health check fails | app crashed on startup | open the **Logs** tab; the traceback is there |

---

## Step 7 — Set your real URL and redeploy (3 min)

Render gives you something like `https://recoverylens.onrender.com`. Copy it.

1. Service → **Environment**
2. Edit `ALLOWED_ORIGINS` → paste your exact URL, no trailing slash
3. **Save** — it redeploys automatically (faster this time, layers are cached)

---

## Step 8 — Deal with the sleeping problem (5 min)

Free instances sleep after ~15 minutes idle. Pick one:

**Option A — pay for it (recommended if the demo depends on the link).**
Service → **Settings** → change instance type to **Starter** (~$7/month). It
never sleeps. Cancel after the finale. This is the only option that actually
removes the risk.

**Option B — keep it awake by hand.** Open the URL 10 minutes before you
present, and again right before you walk up. Works, but relies on you
remembering under pressure.

**Option C — an uptime pinger.** Sign up at a free uptime monitor, point it at
`https://your-url.onrender.com/health` every 10 minutes. Free, but it is one
more thing set up today that you have never tested.

I would pay the $7.

---

## Step 9 — Test the deployed copy properly (15 min)

**Do not assume it works because it went live.** Open your URL and run the full
pre-flight from `docs/FINALE_DEMO.md` — every one of the 14 checks, on the
deployed site, not your laptop.

Pay particular attention to:

- **Creating your account.** You will need the signup code from Step 4. If it
  rejects the code, the environment variable did not save — check Step 5.
- **The discharge autofill.** If it fails, the Anthropic key did not save.
- **Guidance and the refusal.** Confirms the corpus shipped in the image.
- **The prescription upload.** Confirms `pdfplumber` made it in — that package
  was missing from `requirements.txt` until last night.

Check `/health` returns `models_loaded: 6`. If it returns `0`, the model files
did not ship.

---

## Step 10 — Decide what you actually present from

Once the deployed copy passes Step 9, you have two working demos. **Present
from whichever you have tested most**, and keep the other as the fallback:

- Deployed link — impressive, shareable, dies if the venue wifi does
- `RecoveryLens.app` on your laptop — no network, no cold start, no surprises

Have both open before you walk up. Switching costs you one sentence.

---

## Things that will bite you

**The database does not persist.** Render's free tier does not allow a
persistent disk, so the SQLite file lives inside the container and is wiped
whenever the service restarts — including when it wakes from sleep, not just on
redeploy.

In practice: a patient you create during a 10:00 rehearsal is gone by 11:00 if
the service napped in between. **Create your demo data in the same sitting you
present it.** You were going to reset the database before demoing anyway, so
this costs you nothing — it just means doing it last, not first.

Upgrading to the Starter plan ($7) fixes this *and* stops the sleeping. The
exact change is written at the bottom of `render.yaml`.

**The scheduler is off** (`RECOVERYLENS_SCHEDULER=0` in `render.yaml`),
deliberately. A public deployment that starts messaging families on a timer is
not a thing to switch on today. Send from the button.

**It is a public URL.** Ward references, not names — same rule as always, and
now it actually matters.

**Twilio webhooks point at your laptop.** If `TWILIO_WEBHOOK_URL` is still an
ngrok address, inbound replies (including STOP) go to your laptop, not the
deployed copy. Either update it in the Twilio console to
`https://your-url.onrender.com/api/webhooks/twilio`, or demo messaging locally.
Do not do both halves in different places and expect the reply to land.

---

## If it all goes wrong

Open `RecoveryLens.app`. Present locally. Say *"I'll run this from my machine"*
and carry on. Nobody in the room will know, and nothing in your 7 minutes
depends on the link existing.
