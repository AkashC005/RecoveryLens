"""
RecoveryLens — prescription/parse.py

Reads the medicine table off a printed prescription.

WHY THIS IS NOT AN LLM
----------------------
Flattening the PDF to text destroys the thing that matters. A row prints as

    CEFVIL 200 TAB   6   After Food   1        1     3
                                      ^morning ^night ^days

and `pypdf` returns it as `CEFVIL 200 TAB 6 After Food 1 1 3`. The empty Noon
and Evening cells leave no trace, so "1 1 3" is equally consistent with
morning-1/noon-1/days-3 and morning-1/night-1/days-3. Those are different
instructions to a patient.

No amount of prompting fixes that, because the information is genuinely gone.
So the columns are recovered from GEOMETRY instead: `pdfplumber` gives every
word an x-coordinate, the header row gives the column anchors, and a dose is
assigned to the column it physically sits under. The result is deterministic,
free, and reproducible — the same file parses identically every time, which is
not true of anything a model does.

A language model is used for exactly one job, in `transcribe.py`: reading a
PHOTOGRAPH, where there is no text layer to measure. That path is explicitly
marked lower-confidence and the UI says so.

WHAT THIS DELIBERATELY DOES NOT READ
------------------------------------
Name, UHID, address and prescription number are never parsed into the model —
not extracted and discarded, never looked at. `api/database.py` is built on the
rule that a real identifier does not enter the database, and the competition
guidelines forbid displaying identifiable patient data. The only way to keep
that promise is to have no code path that could break it.

REFUSING RATHER THAN GUESSING
-----------------------------
Every cell that is not a recognisable dose token is rejected with a reason, and
shows up in the UI as something the clinician must type themselves. A dose is
the highest-stakes value in this system: a wrong stroke subtype changes a rank,
a wrong dose read back to a family changes what they give someone.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
import re

# Column headers as they appear on the printout. Matched case-insensitively and
# without punctuation, because "Eve." and "Eve" and "EVE" all occur.
HEADERS: dict[str, tuple[str, ...]] = {
    "index":   ("sno", "s.no", "sl", "slno"),
    "name":    ("medicine", "medicinename", "drug", "medication"),
    "qty":     ("qty", "quantity"),
    "take":    ("take", "instruction", "instructions"),
    "morning": ("morning",),
    "noon":    ("noon", "afternoon"),
    "evening": ("eve", "eve.", "evening"),
    "night":   ("night",),
    "days":    ("days", "day", "duration"),
}

# Tamil labels sit under the English ones on the source layout. They are matched
# so the header row is still found when the printout is bilingual, and so a
# Tamil label is never mistaken for a dose value.
TAMIL_HEADERS = {
    "morning": ("காலை",),
    "noon": ("மதியம்",),
    "evening": ("மாலை",),
    "night": ("இரவு",),
}

# A dose cell may hold a whole tablet, a half, or a fraction. Anything else is
# refused rather than coerced.
_DOSE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?|\d/\d|½|¼|¾)$")
_INT = re.compile(r"^\d{1,3}$")

# Where the table ends. Without a terminator the footer ("Timing : 9am to 6pm")
# parses as a medicine with no dose.
_END_MARKERS = ("next visit", "timing", "for appointment", "signature",
                "advice", "review", "follow up", "follow-up")

MAX_COURSE_DAYS = 180
MAX_MEDICINES = 30

FRACTIONS = {"½": "0.5", "¼": "0.25", "¾": "0.75"}
SLOTS = ("morning", "noon", "evening", "night")


@dataclass
class Medication:
    """One row of the table. Doses are strings, not floats, on purpose: "0.5"
    and "1/2" are both things a clinician writes, and rounding either into a
    float loses the distinction between "half a tablet" and a parse error."""

    name: str
    qty: str | None = None
    take: str | None = None
    morning: str | None = None
    noon: str | None = None
    evening: str | None = None
    night: str | None = None
    days: int | None = None
    # Free-text schedules like "SOS" or "if pain" carry no fixed dose times, so
    # they are never turned into a reminder. Flagged rather than dropped — the
    # clinician still needs to see the drug on the list.
    as_needed: bool = False

    @property
    def slots(self) -> list[str]:
        return [s for s in SLOTS if getattr(self, s)]

    @property
    def schedule_text(self) -> str:
        """How the schedule reads to a human: '1 morning, 1 night'."""
        parts = [f"{getattr(self, s)} {s}" for s in self.slots]
        return ", ".join(parts) if parts else ("as needed" if self.as_needed else "no times given")

    @property
    def schedulable(self) -> bool:
        """Whether a daily reminder can be built from this row."""
        return bool(self.slots) and self.days is not None and not self.as_needed

    def to_json(self) -> dict:
        return {
            "name": self.name, "qty": self.qty, "take": self.take,
            "morning": self.morning, "noon": self.noon,
            "evening": self.evening, "night": self.night,
            "days": self.days, "as_needed": self.as_needed,
            "slots": self.slots, "schedule_text": self.schedule_text,
            "schedulable": self.schedulable,
        }


@dataclass
class Prescription:
    medications: list[Medication] = dc_field(default_factory=list)
    diagnosis: str | None = None
    next_visit: date | None = None
    # Cells the parser refused, with the reason. Surfaced to the clinician
    # rather than dropped: "it tried and I would not accept it" is different
    # information from "there was nothing there".
    rejected: list[dict] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    # "pdf_geometry" is deterministic. "image_transcription" went through a
    # model and is not. The UI must show which.
    source: str = "pdf_geometry"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.medications)

    @property
    def course_days(self) -> int | None:
        """Longest course on the sheet — how long reminders should run."""
        lengths = [m.days for m in self.medications if m.days]
        return max(lengths) if lengths else None

    def to_json(self) -> dict:
        return {
            "medications": [m.to_json() for m in self.medications],
            "diagnosis": self.diagnosis,
            "next_visit": self.next_visit.isoformat() if self.next_visit else None,
            "rejected": self.rejected,
            "warnings": self.warnings,
            "source": self.source,
            "error": self.error,
            "course_days": self.course_days,
            "medicine_count": len(self.medications),
        }


def _clean(text: str) -> str:
    """Normalise a header cell for matching.

    Punctuation is removed entirely rather than kept: the source layout prints
    "S.No." and "Eve.", and an earlier version of this that preserved dots
    matched neither, so the row-number column was never anchored and every
    medicine row was silently discarded as unnumbered.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").strip().lower())


def _dose(raw: str) -> str | None:
    """A dose, or None if the cell is not one."""
    token = (raw or "").strip()
    if not token:
        return None
    token = FRACTIONS.get(token, token)
    return token if _DOSE.match(token) else None


def _parse_date(raw: str) -> date | None:
    """Accept the formats this layout actually prints. Never guess a year."""
    text = (raw or "").strip().rstrip(".")
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _columns(words: list[dict]) -> tuple[dict[str, float], float] | None:
    """Find the header row and return {column: x anchor} plus its y position.

    The header row is whichever line contains the most recognised headers. A
    prescription that mentions "Days" in the treatment notes would otherwise
    hijack the search.
    """
    lookup: dict[str, str] = {}
    for col, names in HEADERS.items():
        for n in names:
            lookup[_clean(n)] = col
    for col, names in TAMIL_HEADERS.items():
        for n in names:
            lookup[n.strip()] = col

    by_line: dict[float, list[dict]] = {}
    for w in words:
        key = round(w["top"] / 3.0)          # tolerate sub-point jitter
        by_line.setdefault(key, []).append(w)

    best: tuple[int, float, dict[str, float]] = (0, 0.0, {})
    for line in by_line.values():
        anchors: dict[str, float] = {}
        for w in line:
            col = lookup.get(_clean(w["text"])) or lookup.get(w["text"].strip())
            # First occurrence wins: the Tamil label sits below the English one
            # and must not move the anchor.
            if col and col not in anchors:
                anchors[col] = w["x0"]
        # A real header row has the name column and at least two dose columns.
        dose_cols = sum(1 for c in anchors if c in SLOTS)
        if "name" in anchors and dose_cols >= 2 and len(anchors) > best[0]:
            best = (len(anchors), max(w["top"] for w in line), anchors)

    return (best[2], best[1]) if best[0] else None


#: Slack for kerning, so a word set a hair left of its header still lands in it.
_EDGE_TOLERANCE = 3.0


def _bucket(anchors: dict[str, float]) -> list[tuple[float, float, str]]:
    """Column x-ranges: a column owns from its own anchor to the next one.

    NOT the midpoints between anchors, which is the obvious thing and is wrong.
    The cells are left-aligned under left-aligned headers, so a column's content
    starts at its anchor and runs until the next column starts. Splitting at
    midpoints instead puts the tail of a long value into the following column:
    "PARACETAMOL 650 TAB" lost its "TAB" to the Qty column, and Qty came back
    as "TAB 10".
    """
    ordered = sorted(anchors.items(), key=lambda kv: kv[1])
    out: list[tuple[float, float, str]] = []
    for i, (col, x) in enumerate(ordered):
        left = -1e9 if i == 0 else x - _EDGE_TOLERANCE
        right = 1e9 if i == len(ordered) - 1 else ordered[i + 1][1] - _EDGE_TOLERANCE
        out.append((left, right, col))
    return out


def parse_words(words: list[dict]) -> Prescription:
    """Build a Prescription from positioned words.

    `words` is pdfplumber's `extract_words()` shape: dicts with `text`, `x0`
    and `top`. Taking that rather than a path keeps this function testable with
    a handful of literals and independent of how the page was obtained.
    """
    result = Prescription()
    found = _columns(words)
    if not found:
        result.error = ("could not find the medicine table — no header row with "
                        "a medicine column and dose columns")
        return result

    anchors, header_top = found
    buckets = _bucket(anchors)

    # Group everything below the header into rows.
    rows: dict[float, list[dict]] = {}
    for w in words:
        if w["top"] <= header_top + 4:
            continue
        rows.setdefault(round(w["top"] / 3.0), []).append(w)

    for key in sorted(rows):
        line = sorted(rows[key], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line).strip()
        low = text.lower()

        if any(low.startswith(m) or f" {m}" in low for m in _END_MARKERS):
            if "next visit" in low:
                tail = text.split(":", 1)[-1].strip() if ":" in text else ""
                result.next_visit = _parse_date(tail)
                if tail and result.next_visit is None:
                    result.rejected.append({
                        "field": "next_visit", "value": tail,
                        "reason": "date format not recognised"})
            break

        cells: dict[str, list[str]] = {}
        for w in line:
            for left, right, col in buckets:
                if left <= w["x0"] < right:
                    cells.setdefault(col, []).append(w["text"])
                    break

        name = " ".join(cells.get("name", [])).strip()
        if not name:
            continue
        # The row must be numbered. Without this, a wrapped treatment line
        # lands in the medicine column and becomes a drug.
        idx = " ".join(cells.get("index", [])).strip()
        if not _INT.match(idx):
            continue

        med = Medication(name=name)
        med.qty = " ".join(cells.get("qty", [])).strip() or None
        med.take = " ".join(cells.get("take", [])).strip() or None
        if med.take and re.search(r"sos|if pain|if needed|as needed|prn", med.take, re.I):
            med.as_needed = True

        for slot in SLOTS:
            raw = " ".join(cells.get(slot, [])).strip()
            if not raw:
                continue
            dose = _dose(raw)
            if dose is None:
                result.rejected.append({
                    "medicine": name, "field": slot, "value": raw,
                    "reason": "not a recognisable dose"})
                continue
            setattr(med, slot, dose)

        raw_days = " ".join(cells.get("days", [])).strip()
        if raw_days:
            if _INT.match(raw_days) and 0 < int(raw_days) <= MAX_COURSE_DAYS:
                med.days = int(raw_days)
            else:
                result.rejected.append({
                    "medicine": name, "field": "days", "value": raw_days,
                    "reason": f"not a course length between 1 and {MAX_COURSE_DAYS}"})

        if not med.slots and not med.as_needed:
            result.warnings.append(
                f"{name}: no dose times were read, so no reminder can be built "
                f"for it. Enter the schedule by hand or leave it off.")
        if med.slots and med.days is None:
            result.warnings.append(
                f"{name}: dose times were read but no course length, so "
                f"reminders have no end date.")

        result.medications.append(med)
        if len(result.medications) >= MAX_MEDICINES:
            result.warnings.append(
                f"stopped after {MAX_MEDICINES} medicines — check the document")
            break

    if not result.medications:
        result.error = "found the table but no numbered medicine rows in it"

    # Diagnosis is read for the clinician's context only. It is clinical, not
    # identifying, and it is never sent to a carer.
    #
    # Scanned across ALL lines, not just the ones below the header: the
    # diagnosis is printed above the medicine table, so searching only the rows
    # the table walk produced found it never.
    all_lines: dict[float, list[dict]] = {}
    for w in words:
        all_lines.setdefault(round(w["top"] / 3.0), []).append(w)
    for key in sorted(all_lines):
        text = " ".join(w["text"] for w in sorted(all_lines[key], key=lambda w: w["x0"]))
        if re.match(r"\s*diagnosis\s*[:\-]", text, re.I):
            value = re.split(r"[:\-]", text, maxsplit=1)[-1].strip()
            result.diagnosis = value or None
            break

    return result


def parse_pdf(data: bytes) -> Prescription:
    """Parse a prescription PDF that has a real text layer.

    A scan saved as a PDF has no text layer and yields no words; that returns
    an error rather than an empty prescription, so the caller can fall back to
    the image path instead of silently showing a clinician nothing.
    """
    from io import BytesIO

    import pdfplumber

    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            words: list[dict] = []
            for page in pdf.pages[:3]:      # a prescription is one page; allow for a cover
                words.extend(page.extract_words())
    except Exception as exc:
        out = Prescription()
        out.error = f"could not read the PDF ({type(exc).__name__})"
        return out

    if not words:
        out = Prescription()
        out.error = ("this PDF has no text layer — it is probably a scan. "
                     "Upload it as an image instead.")
        return out

    return parse_words(words)
