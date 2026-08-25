"""
RecoveryLens — extraction/evaluate.py
=====================================
Per-field accuracy for discharge-summary autofill.

    python -m extraction.evaluate

Makes a real API call per case, so it costs a fraction of a cent and needs
ANTHROPIC_API_KEY.

Why per-field and not one number
--------------------------------
A single accuracy figure would hide the only thing worth knowing. `age` and
`systolic_bp` are written as numbers next to a label and should be near-perfect.
The deficits are described in prose — "right upper limb power 3/5", "dense left
hemiplegia involving face, arm and leg" — and are where this will fail. Averaging
those together produces a number that is true and useless.

Why THREE outcomes and not two
------------------------------
    correct   the value matches the gold label
    wrong     a value was extracted and it disagrees
    missed    the field was in the summary and nothing was extracted

    spurious  a field was extracted that a careful human would have left blank

`missed` and `wrong` cost completely different amounts. A missed field costs a
clinician five seconds of typing. A wrong field silently changes a risk tier and
nobody knows to question it. Reporting them as one "error rate" would let a
change that trades ten misses for one wrong value look like an improvement.

`spurious` is tracked separately because it is the failure this design exists to
prevent: a plausible value invented from an absent fact.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field as dc_field
import json
import sys
from pathlib import Path

from .extract import EXTRACTABLE_FIELDS, extract_from_text

CASES = Path(__file__).resolve().parent / "goldset" / "cases.json"


@dataclass
class FieldTally:
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    spurious: int = 0
    examples: list[str] = dc_field(default_factory=list)

    @property
    def expected(self) -> int:
        return self.correct + self.wrong + self.missed

    @property
    def precision(self) -> float | None:
        """Of the values it gave, how many were right.

        The number that matters most: it is the probability that a filled field
        is trustworthy, which is what a clinician is implicitly deciding when
        they skim rather than check.
        """
        given = self.correct + self.wrong + self.spurious
        return self.correct / given if given else None

    @property
    def recall(self) -> float | None:
        """Of the values that were there to find, how many it got."""
        return self.correct / self.expected if self.expected else None


def _equal(expected, actual) -> bool:
    """Compare a gold value with an extracted one.

    Numbers are compared numerically so 6.5 and "6.5" agree, because the model
    returns JSON and the gold file is hand-written; a type mismatch there is
    noise, not a finding.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) is bool(actual)
    if isinstance(expected, (int, float)):
        try:
            return abs(float(expected) - float(actual)) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(expected).strip().lower() == str(actual).strip().lower()


def evaluate(cases: list[dict]) -> dict[str, FieldTally]:
    tallies: dict[str, FieldTally] = defaultdict(FieldTally)

    for case in cases:
        expected = case["expected"]
        result = extract_from_text(case["text"])
        if not result.ok:
            print(f"  {case['id']}: FAILED — {result.error}", file=sys.stderr)
            continue
        actual = result.as_form_values()

        for name in EXTRACTABLE_FIELDS:
            tally = tallies[name]
            want, got = expected.get(name), actual.get(name)

            if name in expected and name in actual:
                if _equal(want, got):
                    tally.correct += 1
                else:
                    tally.wrong += 1
                    tally.examples.append(f"{case['id']}: wanted {want!r}, got {got!r}")
            elif name in expected:
                tally.missed += 1
            elif name in actual:
                # Extracted something a careful reader would have left blank.
                # The failure this whole design exists to prevent.
                tally.spurious += 1
                tally.examples.append(f"{case['id']}: invented {got!r}")

        print(f"  {case['id']}: {len(actual)} fields, "
              f"{len(result.rejected)} rejected")

    return dict(tallies)


def report(tallies: dict[str, FieldTally]) -> int:
    rows = [(n, t) for n, t in tallies.items() if t.expected or t.spurious]
    rows.sort(key=lambda r: (r[1].precision if r[1].precision is not None else 2,
                             -r[1].expected))

    print(f"\n{'field':24} {'n':>3} {'ok':>3} {'wrong':>5} {'miss':>5} "
          f"{'extra':>5}  {'prec':>5} {'rec':>5}")
    print("-" * 68)

    for name, t in rows:
        prec = f"{t.precision:.2f}" if t.precision is not None else "  — "
        rec = f"{t.recall:.2f}" if t.recall is not None else "  — "
        print(f"{name:24} {t.expected:>3} {t.correct:>3} {t.wrong:>5} "
              f"{t.missed:>5} {t.spurious:>5}  {prec:>5} {rec:>5}")

    correct = sum(t.correct for t in tallies.values())
    wrong = sum(t.wrong for t in tallies.values())
    missed = sum(t.missed for t in tallies.values())
    spurious = sum(t.spurious for t in tallies.values())
    expected = correct + wrong + missed

    print("-" * 68)
    print(f"{'TOTAL':24} {expected:>3} {correct:>3} {wrong:>5} "
          f"{missed:>5} {spurious:>5}")

    if expected:
        print(f"\nOf the {expected} values present in the summaries, it found "
              f"{correct} correctly ({correct / expected:.0%}).")
    given = correct + wrong + spurious
    if given:
        print(f"Of the {given} values it filled in, {correct} were right "
              f"({correct / given:.0%}).")

    print(f"\nThe two numbers that must not be averaged:")
    print(f"  missed   {missed:>3}  — costs a clinician a few seconds of typing")
    print(f"  wrong    {wrong:>3}  — silently changes a risk tier")
    print(f"  invented {spurious:>3}  — a value from a fact not in the document")

    problems = [(n, t) for n, t in rows if t.wrong or t.spurious]
    if problems:
        print("\nWhere it went wrong:")
        for name, t in problems:
            for example in t.examples[:3]:
                print(f"  {name:22} {example}")

    # Non-zero exit if anything was invented. A missed field is a cost; an
    # invented one is a defect, and this should be usable in CI.
    return 1 if spurious else 0


def main() -> int:
    from config import load_env
    load_env()

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    cases = payload["cases"]
    print(f"Evaluating {len(cases)} discharge summaries "
          f"({sum(len(c['expected']) for c in cases)} labelled values)\n")

    tallies = evaluate(cases)
    if not tallies:
        print("Nothing evaluated.", file=sys.stderr)
        return 1
    return report(tallies)


if __name__ == "__main__":
    raise SystemExit(main())
