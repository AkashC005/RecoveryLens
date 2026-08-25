#!/usr/bin/env python3
"""
Send a check-in to the carer, without typing JSON at a shell prompt.

    python scripts/send_checkin.py

Why this exists
---------------
The curl equivalent is one long line containing eight quote characters, and
pasting it through any app that auto-formats text turns `"` into `”`. The shell
then treats the curly quote as an ordinary character, the JSON fails to parse,
and the error points at a column number. That is a bad thirty seconds in front of
an audience, and it is entirely avoidable: this script builds the JSON itself.

It also does what the curl sequence did in three commands — log in, find the due
check-ins, send one — and prints the result in a form you can read out.
"""

from getpass import getpass
import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8000"


class Client:
    """Just enough HTTP to log in and hold the session cookie."""

    def __init__(self, base: str = BASE):
        self.base = base.rstrip("/")
        self.cookie = ""

    def request(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                # The session arrives as a Set-Cookie header and every later
                # request needs it — this is what `curl -c/-b` was doing.
                for header, value in resp.getheaders():
                    if header.lower() == "set-cookie":
                        self.cookie = value.split(";", 1)[0]
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            raise SystemExit(f"\n{exc.code} — {detail}\n")
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"\nCannot reach {self.base} — is the server running?\n"
                f"  uvicorn api.main:app --reload --port 8000\n\n({exc.reason})\n")


def main() -> int:
    client = Client()

    print("Sign in as the clinician who owns the patient.\n")
    email = input("  Email:    ").strip()
    password = getpass("  Password: ")

    me = client.request("POST", "/api/auth/login",
                        {"email": email, "password": password})
    print(f"\nSigned in as {me['email']}\n")

    # include_scheduled: the first real check-in is day 3, so without it a fresh
    # assessment shows nothing and the demo stalls on an empty list.
    due = client.request("GET", "/api/checkins/due?include_scheduled=true")
    if not due:
        raise SystemExit("No check-ins found. Run an assessment first.\n")

    print("Check-ins waiting:\n")
    for i, c in enumerate(due, 1):
        who = c.get("patient_ref") or f"patient {c['patient_id']}"
        print(f"  {i}. {who}  —  due {c['scheduled_for'][:10]}  (id {c['id']})")

    choice = input(f"\nWhich one? [1-{len(due)}, Enter for 1]: ").strip() or "1"
    try:
        target = due[int(choice) - 1]
    except (ValueError, IndexError):
        raise SystemExit("Not one of the options.\n")

    print(f"\nSending check-in {target['id']} …\n")
    result = client.request("POST", f"/api/checkins/{target['id']}/send")

    if result.get("sent"):
        print(f"  SENT via {result['channel']}  (message {result['message_id']})")
        audio = result.get("audio") or {}
        if result.get("media_url"):
            print(f"  audio    attached, {audio.get('bytes', 0):,} bytes")
        elif audio.get("reason"):
            print(f"  audio    not attached — {audio['reason']}")
        language = (result.get("translation") or {}).get("language", "en")
        if language != "en":
            print(f"  language {language}")
        print("\n  Check the phone.\n")
    else:
        # Two different failures with two different fixes, so they are reported
        # separately: `reason` is OUR policy gate refusing, `error` is Twilio's.
        if result.get("reason"):
            print(f"  NOT SENT — our policy gate refused:\n    {result['reason']}\n")
        else:
            print(f"  NOT SENT — Twilio refused:\n    {result.get('error')}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
