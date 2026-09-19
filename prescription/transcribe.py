"""
RecoveryLens — prescription/transcribe.py

The photograph path, for when there is no text layer to measure.

READ parse.py FIRST. A PDF with a text layer is parsed from column geometry:
deterministic, reproducible, no model involved. That is the path to prefer and
the path to demonstrate.

This file exists because a clinic will hand you a photograph, and refusing to
read it is not a product. But it is strictly worse, and the difference is not a
detail:

  * A model reads the columns. There is no x-coordinate to check it against, so
    "which column is this 1 in" becomes a judgement rather than a measurement.
  * It is not reproducible. The same photograph can parse differently twice —
    the API exposes no sampling controls for this model, so that variance
    cannot be turned off.
  * A blurred or cropped cell produces a confident value, not a gap.

So everything from this path is marked `needs_review`, the UI says where it came
from, and `source` is stored on the record for whoever reads it in six months.

The one safety property that survives is the important one: nothing from either
path reaches a carer until a clinician has confirmed the row against the paper.
"""

from __future__ import annotations

import base64
import json
import os
import re

from .parse import MAX_COURSE_DAYS, SLOTS, Medication, Prescription

MAX_IMAGE_BYTES = 6 * 1024 * 1024
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

SYSTEM = """You transcribe the medicine table from a photographed prescription.

You are reading a table. Each row is one medicine, with columns for quantity, \
when to take it (before or after food), and a dose in some or all of four time \
columns — Morning, Noon, Evening, Night — then a number of days.

THE COLUMNS ARE THE WHOLE TASK. A "1" means nothing until you know which column \
it sits in. Read down from the header to work out which column each number is \
under. If a cell is empty, omit that field — an empty Noon column means no \
midday dose, and inventing one changes what a family gives someone.

If you cannot read a cell confidently, OMIT it. A blank field costs a clinician \
a few seconds of typing. A wrong dose does not get noticed.

Do NOT transcribe the patient's name, hospital number, address or prescription \
number. They are not wanted and must not appear in your reply.

Reply with a JSON object only, no preamble and no markdown fences:

{"medications": [{"name": "CEFVIL 200 TAB", "qty": "6", "take": "After Food",
                  "morning": "1", "night": "1", "days": 3}],
 "diagnosis": "WART LEFT FOOT",
 "next_visit": "2026-09-21"}

`next_visit` must be ISO format, or omitted if there is no next-visit date. \
Doses are strings ("1", "0.5"). `days` is a whole number."""


class TranscriptionUnavailable(RuntimeError):
    """No key, no SDK, or the provider failed. The clinician can still type."""


def transcription_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _model() -> str:
    return os.getenv("RECOVERYLENS_LLM_MODEL", "claude-sonnet-5")


def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def transcribe_image(data: bytes, content_type: str) -> Prescription:
    """Read a photographed prescription. Never raises — a failure is an empty
    table with a stated reason, and the clinician types it in."""
    out = Prescription(source="image_transcription")

    if content_type not in ALLOWED_TYPES:
        out.error = (f"{content_type} is not an image format this reads "
                     f"({', '.join(sorted(ALLOWED_TYPES))})")
        return out
    if not data:
        out.error = "the image was empty"
        return out
    if len(data) > MAX_IMAGE_BYTES:
        out.error = f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB"
        return out
    if not transcription_enabled():
        out.error = "reading a photograph needs ANTHROPIC_API_KEY; enter the medicines by hand"
        return out

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())
        resp = client.messages.create(
            model=_model(), max_tokens=2000, system=SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg" if content_type == "image/jpg" else content_type,
                    "data": base64.b64encode(data).decode("ascii")}},
                {"type": "text", "text": "Transcribe the medicine table."},
            ]}],
        )
        payload = _parse_json("\n".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"))
    except Exception as exc:
        out.error = f"could not read the photograph ({type(exc).__name__}: {exc})"[:200]
        return out

    out.diagnosis = (payload.get("diagnosis") or "").strip() or None

    raw_visit = (payload.get("next_visit") or "").strip()
    if raw_visit:
        from datetime import date
        try:
            out.next_visit = date.fromisoformat(raw_visit)
        except ValueError:
            out.rejected.append({"field": "next_visit", "value": raw_visit,
                                 "reason": "not an ISO date"})

    for item in payload.get("medications", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        med = Medication(name=name)
        med.qty = (str(item.get("qty", "")) or "").strip() or None
        med.take = (str(item.get("take", "")) or "").strip() or None
        if med.take and re.search(r"sos|if pain|if needed|as needed|prn", med.take, re.I):
            med.as_needed = True

        for slot in SLOTS:
            raw = item.get(slot)
            if raw in (None, ""):
                continue
            # The same dose grammar the deterministic parser enforces. A model
            # writing "1-0-1" into a single cell is rejected here rather than
            # reaching a carer as a dose.
            from .parse import _dose
            dose = _dose(str(raw))
            if dose is None:
                out.rejected.append({"medicine": name, "field": slot,
                                     "value": str(raw),
                                     "reason": "not a recognisable dose"})
                continue
            setattr(med, slot, dose)

        days = item.get("days")
        if days not in (None, ""):
            try:
                n = int(days)
                if 0 < n <= MAX_COURSE_DAYS:
                    med.days = n
                else:
                    raise ValueError
            except (TypeError, ValueError):
                out.rejected.append({"medicine": name, "field": "days",
                                     "value": str(days),
                                     "reason": f"not a course length between 1 and {MAX_COURSE_DAYS}"})

        out.medications.append(med)

    if not out.medications and not out.error:
        out.error = "no medicines could be read from the photograph"

    if out.medications:
        out.warnings.insert(0,
            "Read from a photograph by a model, not measured from the document. "
            "Check every dose against the paper before confirming — this path "
            "cannot verify which column a number came from.")
    return out
