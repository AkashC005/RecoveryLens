# Shipping RecoveryLens

Two ways to run it, and they answer different questions.

| | Local app | Deployed link |
|---|---|---|
| What it is | `RecoveryLens.app` — double-click, browser opens | `https://…onrender.com` |
| Who can use it | You, on this Mac | Anyone with the link *and the signup code* |
| Depends on the network | No | Entirely |
| Right for | Tomorrow's demo | Sharing afterwards |

**Build the local app first and test it, whatever else you do.** A demo that
depends on infrastructure deployed hours earlier is a demo with a single point
of failure you have never seen fail.

---

## 1. The local app

One-time setup:

```bash
cd ~/Desktop/Project/"Recovery Lens"

# The virtualenv (skip if .venv already works)
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# The frontend, built to static files the API will serve
cd web && npm install && npm run build && cd ..
```

Then double-click **RecoveryLens.app**. It starts the server, waits for it to
answer, and opens `http://localhost:8000`. Closing the Terminal window stops it.

Drag the `.app` to your Desktop or Dock if you like — it finds the project by
walking up from its own location, so **keep it inside the project folder**, or
set `RECOVERYLENS_HOME` to the project path if you move it.

It refuses to start with a named reason if the virtualenv is missing, the
frontend is not built, or port 8000 is already in use. Those are the three
things that actually go wrong, and each one otherwise shows up as a blank page
half a minute later.

### Giving the app an icon

macOS shows a generic icon until you give it one:

1. Make a 1024×1024 PNG.
2. `mkdir icon.iconset && sips -z 512 512 icon.png --out icon.iconset/icon_512x512.png`
   (repeat for 16, 32, 128, 256, 512 and their `@2x` variants)
3. `iconutil -c icns icon.iconset -o RecoveryLens.app/Contents/Resources/icon.icns`

Cosmetic. Do it after everything else works.

---

## 2. The deployed link

`render.yaml` and `Dockerfile` are in the repository root. The Dockerfile builds
the frontend in a throwaway stage and copies only the output, so Node never
ships in the runtime image.

### Before you deploy — the part that matters

**Registration is open by default.** That is correct for a laptop and wrong for
a URL anyone can find: without a code, anyone who reaches the link can create an
account. Setting `RECOVERYLENS_SIGNUP_CODE` is not optional on a public
deployment.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

Keep that string. It is what you type into the sign-up form, and what you give a
judge who wants to look around afterwards.

### Steps

1. Push the repository to GitHub. **Confirm `.env` is not in it:**
   `git log --oneline -- .env` must print nothing.
2. Render → **New** → **Blueprint** → point it at the repo. It reads
   `render.yaml`.
3. Render prompts for every `sync: false` variable. Paste:
   - `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
   - `RECOVERYLENS_SIGNUP_CODE` — the string you just generated
   - Twilio keys **only** if you intend to send real messages from the deployed
     copy. Leave them blank otherwise; the app runs without them.
4. Change `ALLOWED_ORIGINS` to the URL Render gives you.
5. Deploy. First build takes several minutes — most of it is `pip install`.
6. Open the URL, register with the code, and walk the whole demo path on the
   deployed copy before you trust it.

### Things that will bite you

**Free instances sleep.** After about 15 minutes idle, the next request waits
~50 seconds for a cold start. That is catastrophic timing in a 7-minute pitch.
Open the URL a few minutes before you present, and again just before you walk
up. If the demo depends on it, pay for the instance that does not sleep.

**The database is a file on the mounted disk.** The `disk:` block in
`render.yaml` is what stops every patient disappearing on restart. Without it
SQLite lives on ephemeral storage. One instance only — SQLite does not want two
writers, which is the same reason the container runs a single worker.

**The scheduler is off** (`RECOVERYLENS_SCHEDULER=0`), deliberately. A public
deployment that starts messaging families on a timer is not something to enable
the night before a pitch. Send from the button in the UI instead.

**Nothing real goes in.** It is a public URL. Ward references, not names —
which is what `patient_ref` is for, and what the sign-in screen already says.

---

## If the deployed copy misbehaves tomorrow

Open `RecoveryLens.app` and present locally. Nothing in the pitch depends on the
link existing, and swapping to it costs you one sentence: *"I'll run this
locally."* Nobody in the room will know or care.
