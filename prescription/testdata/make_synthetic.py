"""Generate synthetic prescriptions for development and demo.

WHY THIS FILE EXISTS
--------------------
The layout this feature parses was taken from a real clinic's printout, which
carried a patient's name, hospital number, address and the operating surgeon's
medical registration number. That document is not in this repository and must
never be: the competition guidelines forbid displaying identifiable patient
data, and `api/database.py` is built on the rule that a real identifier never
enters the database.

So the parser is developed against invented patients on the same layout. Every
name, number and address below is fictional. The clinic name is fictional. There
is no registration number, because a real-looking one belongs to a real doctor.

Run:  python -m prescription.testdata.make_synthetic
"""

from __future__ import annotations

import pathlib

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ModuleNotFoundError as exc:  # pragma: no cover - a setup message, not logic
    raise SystemExit(
        "This script needs reportlab, which is a development dependency and is "
        "deliberately not in requirements.txt — nothing the API serves imports "
        "it.\n\n    pip install -r requirements-dev.txt\n"
    ) from exc

HERE = pathlib.Path(__file__).resolve().parent

# Four cases chosen to break a naive parser rather than to flatter it.
CASES = [
    {
        "file": "prescription_basic.pdf",
        "uhid": "SYN0001", "presc": "Q000101",
        "name": "Mr. A. SAMPLE (48 y. / M)", "addr": "SYNTHETIC CITY",
        "date": "18-09-2026", "next_visit": "21-Sep-2026",
        "diagnosis": "WART LEFT FOOT",
        "treatment": ["UNDER LA, WART EXCISED.", "SUTURED WITH 2-0 ETHILON",
                      "DRESSING DONE"],
        # name, qty, take, morning, noon, eve, night, days
        "meds": [
            ("CEFVIL 200 TAB", "6", "After Food", "1", "", "", "1", "3"),
            ("COMBIFLAM TAB", "6", "After Food", "1", "", "", "1", "3"),
            ("PANTOVIL 40 TAB", "3", "Before Food", "1", "", "", "", "3"),
        ],
    },
    {
        "file": "prescription_three_times.pdf",
        "uhid": "SYN0002", "presc": "Q000102",
        "name": "Mrs. B. EXAMPLE (61 y. / F)", "addr": "SYNTHETIC CITY",
        "date": "18-09-2026", "next_visit": "25-Sep-2026",
        "diagnosis": "DIABETIC FOOT ULCER RIGHT GREAT TOE",
        "treatment": ["WOUND DEBRIDEMENT DONE.", "DAILY DRESSING ADVISED"],
        "meds": [
            # Three slots a day, and a 7-day course: exercises the noon column
            # and a duration longer than the 3 days of the basic case.
            ("AUGMENTIN 625 TAB", "21", "After Food", "1", "1", "", "1", "7"),
            ("METFORMIN 500 TAB", "14", "After Food", "1", "", "", "1", "7"),
            ("PREGABALIN 75 CAP", "7", "Before Food", "", "", "", "1", "7"),
            ("SHELCAL 500 TAB", "7", "After Food", "", "1", "", "", "7"),
        ],
    },
    {
        "file": "prescription_sparse.pdf",
        "uhid": "SYN0003", "presc": "Q000103",
        "name": "Mr. C. TESTCASE (34 y. / M)", "addr": "SYNTHETIC CITY",
        "date": "18-09-2026", "next_visit": "",
        "diagnosis": "INGROWN TOENAIL",
        "treatment": ["NAIL AVULSION DONE"],
        # One medicine, no next visit, no timing in some columns. The parser
        # must not invent a next-visit date, and must not invent a noon dose.
        "meds": [
            ("PARACETAMOL 650 TAB", "10", "After Food", "1", "1", "", "1", "5"),
        ],
    },
    {
        "file": "prescription_awkward.pdf",
        "uhid": "SYN0004", "presc": "Q000104",
        "name": "Ms. D. EDGECASE (72 y. / F)", "addr": "SYNTHETIC CITY",
        "date": "18-09-2026", "next_visit": "02-Oct-2026",
        "diagnosis": "CELLULITIS LEFT LEG",
        "treatment": ["IV ANTIBIOTICS GIVEN.", "LIMB ELEVATION ADVISED"],
        "meds": [
            # A half-dose, a twice-daily with a decimal, an SOS line with no
            # fixed schedule, and a drug name containing a number that is not
            # a dose. All four are ways a real prescription defeats a regex.
            ("DEFLAZACORT 6 TAB", "10", "After Food", "0.5", "", "", "0.5", "10"),
            ("VITAMIN B12 TAB", "10", "After Food", "1", "", "", "", "10"),
            ("DOLO 650 TAB", "10", "SOS / If pain", "", "", "", "", "10"),
        ],
    },
]

CLINIC = "SYNTHETIC FOOT & WOUND CARE CENTRE"
ADDRESS = "00 Example Building, Test Road, Synthetic City-000000"


def draw(case: dict, path: pathlib.Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w / 2, h - 28 * mm, CLINIC)
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(w / 2, h - 34 * mm, ADDRESS)
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(w / 2, h - 39 * mm,
                        "SYNTHETIC DOCUMENT — FICTIONAL PATIENT — NOT A REAL PRESCRIPTION")

    c.line(18 * mm, h - 43 * mm, w - 18 * mm, h - 43 * mm)

    y = h - 52 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "PATIENT INFORMATION")
    c.setFont("Helvetica", 9)
    for label, value in [("UHID", case["uhid"]), ("Name", case["name"]),
                         ("Addr", case["addr"]), ("Presc", case["presc"])]:
        y -= 6 * mm
        c.drawString(20 * mm, y, f"{label} : {value}")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(w - 70 * mm, h - 58 * mm, f"Date : {case['date']}")

    # Vitals left blank, exactly as the source layout does. A parser that fills
    # these in has invented them.
    c.setFont("Helvetica", 9)
    vy = h - 58 * mm
    for label in ["Height (cm) :", "BP (mmHg) :", "Temp. (F) :", "RR (bpm) :"]:
        c.drawString(w - 120 * mm, vy, label)
        vy -= 6 * mm

    y -= 12 * mm
    c.line(18 * mm, y, w - 18 * mm, y)
    y -= 8 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, "Complaints    :")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Diagnosis     :   {case['diagnosis']}")
    y -= 6 * mm
    c.drawString(20 * mm, y, "Treatment     :")
    for line in case["treatment"]:
        c.drawString(52 * mm, y, line)
        y -= 5.5 * mm

    y -= 4 * mm
    c.line(18 * mm, y, w - 18 * mm, y)

    y -= 8 * mm
    cols = [20, 34, 92, 106, 130, 148, 162, 176, 190]
    heads = ["S.No.", "Medicine Name", "Qty", "Take",
             "Morning", "Noon", "Eve.", "Night", "Days"]
    c.setFont("Helvetica-Bold", 8.5)
    for x, head in zip(cols, heads):
        c.drawString(x * mm, y, head)

    c.setFont("Helvetica", 8.5)
    for i, med in enumerate(case["meds"], start=1):
        y -= 6.5 * mm
        name, qty, take, morning, noon, eve, night, days = med
        for x, cell in zip(cols, [str(i), name, qty, take, morning, noon, eve, night, days]):
            c.drawString(x * mm, y, cell)

    if case["next_visit"]:
        y -= 14 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, f"NEXT VISIT : {case['next_visit']}")

    c.setFont("Helvetica", 8)
    c.drawCentredString(w / 2, 18 * mm, "Timing : 9am to 6pm")
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(w / 2, 13 * mm,
                        "Synthetic test fixture. No real patient, clinician or clinic.")
    c.save()


def main() -> None:
    for case in CASES:
        path = HERE / case["file"]
        draw(case, path)
        print(f"wrote {path.name}  ({len(case['meds'])} medicines)")


if __name__ == "__main__":
    main()
