"""
RecoveryLens — ml/07_conformal.py
=================================
Uncertainty intervals via Inductive Venn-Abers Predictors (IVAP).

Run:  python ml/07_conformal.py

Why Venn-Abers and not standard split-conformal
-----------------------------------------------
Split-conformal for classification emits LABEL SETS - {0}, {1}, or {0,1}. That is
the wrong shape here. RecoveryLens outputs a risk probability that drives a tier,
so what a clinician needs is an interval around that probability, not a set of
labels. Venn-Abers is the conformal-family method that produces exactly that,
with a validity guarantee that does not assume the model is well specified.

It also uses isotonic regression, which sklearn already ships. No new dependency,
which matters given the 512MB deploy target.

Why no refitting
----------------
The models were fit on the training split. A conformal guarantee needs a
calibration set the model has never seen, so the existing TEST split is divided
in half: calibration and evaluation. The model saw neither. Refitting to create a
calibration set would invalidate every number already reported in
outputs/final_metrics.json.

What IVAP does, concretely
--------------------------
Given calibration scores s_1..s_n with labels y_1..y_n, and a new score s:

  p0 = isotonic fit on {(s_i, y_i)} plus the hypothetical (s, 0), read at s
  p1 = isotonic fit on {(s_i, y_i)} plus the hypothetical (s, 1), read at s

The truth is bracketed by [p0, p1] - the interval is what the calibration data
permits regardless of which label the new point turns out to have.

WHAT INTERVAL WIDTH DOES AND DOES NOT MEAN
------------------------------------------
An earlier version of this file claimed width tracks model weakness, so that a
poor model like recurrence_14d (AUC 0.59) would show visibly wide intervals.
THAT IS FALSE, and a synthetic test disproved it:

    strong signal (AUC ~0.85)  ->  mean width 0.0222
    weak signal   (AUC ~0.55)  ->  mean width 0.0069

Weak signal gives NARROWER intervals, not wider. With little signal the isotonic
fit is nearly flat, so one hypothetical extra point barely moves it. Width
measures how much a single observation could shift the local calibration curve -
a function of calibration density and local slope, not of discriminative power.

What width DOES track is epistemic uncertainty from finite calibration data:

    n=200 -> 0.0824      n=1000 -> 0.0213      n=4000 -> 0.0089

So read a wide interval as "few calibration examples near this score", never as
"this model is weak". For model weakness, read the AUC and the actionability
tier in api/artifacts/thresholds.json.

WHY THIS IS STILL WORTH SHIPPING
--------------------------------
External validation on IST-3 measured calibration-in-the-large at -0.75
(poor_outcome_6m) and -0.58 (death_14d): the raw probabilities are systematically
too high. Venn-Abers is isotonic recalibration with a validity guarantee, which
addresses exactly that, and the error bars are honest about how well the
calibration itself is determined.

Deployment
----------
Fitting isotonic twice per prediction is too slow for an API. Instead this script
precomputes the interval over a fine grid of scores and writes it to
api/artifacts/venn_abers.json. At inference the API interpolates - microseconds,
no sklearn call, no calibration data shipped to production.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
MODELS = ROOT / "models"
ARTIFACTS = ROOT / "api" / "artifacts"

SEED = 42
GRID_SIZE = 400          # score points at which the interval is precomputed
MIN_CAL_EVENTS = 30      # below this, coverage estimates are not reported

OUTCOMES = ["death_14d", "recurrence_14d", "haemorrhage_14d",
            "pe_14d", "poor_outcome_6m", "nonadherence_6m"]

FEATURES = (
    ["AGE", "RDELAY", "RSBP", "DEFCOUNT", "DEFUNASSESSED"]
    + ["SEX", "RXASP", "RCONSC_ord"] + [f"RDEF{i}" for i in range(1, 9)]
    + ["RSLEEP", "RCT", "RVISINF", "RHEP24", "RASP3"]
    + ["STYPE", "RATRIAL_cat", "RXHEP"]
)


# --------------------------------------------------------------------------- #
def venn_abers_interval(cal_scores: np.ndarray, cal_labels: np.ndarray,
                        s: float) -> tuple[float, float]:
    """Interval [p0, p1] for a single new score s.

    p0 assumes the new point is a negative, p1 that it is a positive. The true
    calibrated probability lies between them, whichever it turns out to be.
    """
    x0 = np.append(cal_scores, s)
    x1 = np.append(cal_scores, s)
    y0 = np.append(cal_labels, 0.0)
    y1 = np.append(cal_labels, 1.0)

    iso0 = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso1 = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    p0 = float(iso0.fit(x0, y0).predict([s])[0])
    p1 = float(iso1.fit(x1, y1).predict([s])[0])

    # p0 <= p1 holds mathematically; clamp against floating-point noise only.
    return (min(p0, p1), max(p0, p1))


def merged_probability(p0: float, p1: float) -> float:
    """Venn-Abers point estimate. Reduces to p when p0 == p1."""
    denom = 1.0 - p0 + p1
    return p1 / denom if denom > 0 else 0.5


def build_grid(cal_scores: np.ndarray, cal_labels: np.ndarray,
               size: int = GRID_SIZE) -> dict:
    """Precompute intervals across the score range so the API never fits isotonic.

    Cost is O(size) isotonic fits, done once here rather than per request.
    """
    lo, hi = float(cal_scores.min()), float(cal_scores.max())
    # Extend slightly beyond the observed range; production scores can fall
    # outside what calibration happened to contain.
    pad = max(0.01, (hi - lo) * 0.05)
    grid = np.linspace(max(0.0, lo - pad), min(1.0, hi + pad), size)

    p0s, p1s = [], []
    for s in grid:
        a, b = venn_abers_interval(cal_scores, cal_labels, float(s))
        p0s.append(round(a, 6))
        p1s.append(round(b, 6))

    return {"grid": [round(float(g), 6) for g in grid], "p0": p0s, "p1": p1s}


def _calibration_error(raw: np.ndarray, venn: np.ndarray, y: np.ndarray,
                       n_bins: int = 10) -> tuple[float, float, int]:
    """Mean absolute gap between predicted and observed rate, per equal-count bin.

    Both predictors are scored on the SAME bins, defined by the raw score, so the
    comparison is like for like. Bins with fewer than 20 patients are dropped —
    below that the observed rate is too noisy to be a useful target.
    """
    try:
        bins = pd.qcut(raw, q=n_bins, duplicates="drop", labels=False)
    except ValueError:
        return float("nan"), float("nan"), 0

    raw_err = venn_err = 0.0
    used = 0
    for b in np.unique(bins):
        m = bins == b
        if m.sum() < 20:
            continue
        observed = float(y[m].mean())
        raw_err += abs(float(raw[m].mean()) - observed)
        venn_err += abs(float(venn[m].mean()) - observed)
        used += 1
    return raw_err, venn_err, used


def interpolate(artifact: dict, s: float) -> tuple[float, float]:
    """The inference-time path. Mirrors what the API will do."""
    g = np.asarray(artifact["grid"])
    p0 = float(np.interp(s, g, artifact["p0"]))
    p1 = float(np.interp(s, g, artifact["p1"]))
    return min(p0, p1), max(p0, p1)


# --------------------------------------------------------------------------- #
def main() -> int:
    clean = OUT / "ist1_clean.csv"
    splits = OUT / "split_indices.csv"
    if not clean.exists() or not splits.exists():
        print("Run the notebook first — need ist1_clean.csv and split_indices.csv",
              file=sys.stderr)
        return 1

    data = pd.read_csv(clean).join(pd.read_csv(splits, index_col="row"))
    test = data[data.split == "test"]

    rng = np.random.default_rng(SEED)
    idx = test.index.to_numpy().copy()
    rng.shuffle(idx)
    cal_idx, eval_idx = idx[: len(idx) // 2], idx[len(idx) // 2:]
    print(f"Test {len(test):,} -> calibration {len(cal_idx):,} / "
          f"evaluation {len(eval_idx):,}\n")

    artifacts, report = {}, []

    for outcome in OUTCOMES:
        path = MODELS / f"final_{outcome}.pkl"
        if not path.exists():
            print(f"{outcome:18s} missing model, skipped")
            continue

        model = joblib.load(path)

        cal = data.loc[cal_idx].dropna(subset=[outcome])
        ev = data.loc[eval_idx].dropna(subset=[outcome])
        cal_s = model.predict_proba(cal[FEATURES])[:, 1]
        cal_y = cal[outcome].to_numpy(dtype=float)
        n_events = int(cal_y.sum())

        artifacts[outcome] = build_grid(cal_s, cal_y)
        artifacts[outcome]["calibration_events"] = n_events
        artifacts[outcome]["calibration_n"] = len(cal_y)

        # --- evaluation on the untouched half -----------------------------
        ev_s = model.predict_proba(ev[FEATURES])[:, 1]
        ev_y = ev[outcome].to_numpy(dtype=float)

        lo_hi = np.array([interpolate(artifacts[outcome], float(s)) for s in ev_s])
        widths = lo_hi[:, 1] - lo_hi[:, 0]
        merged = np.array([merged_probability(a, b) for a, b in lo_hi])

        # DOES RECALIBRATION ACTUALLY HELP?
        #
        # An earlier version asked whether the interval contained the observed
        # event rate per bin. That test was wrong twice over. Venn-Abers
        # guarantees nothing of the sort — its guarantee concerns the
        # multiprobability prediction, not coverage of empirical frequencies —
        # and the test was arithmetically doomed anyway: intervals are ~0.01
        # wide while a bin rate from ~194 patients carries a sampling standard
        # error near 0.02. The noise exceeded the quantity under test, so it
        # would have failed on a perfect predictor.
        #
        # The question worth asking is the one recalibration is for: are the
        # merged probabilities closer to observed frequencies than the raw ones?
        raw_ece, venn_ece, n_bins = _calibration_error(ev_s, merged, ev_y)

        reliable = n_events >= MIN_CAL_EVENTS
        row = {
            "outcome": outcome,
            "calibration_n": len(cal_y),
            "calibration_events": n_events,
            "mean_interval_width": round(float(widths.mean()), 4),
            "median_interval_width": round(float(np.median(widths)), 4),
            "max_interval_width": round(float(widths.max()), 4),
            "raw_calibration_error": round(raw_ece, 4),
            "venn_calibration_error": round(venn_ece, 4),
            "improvement_pct": (round((1 - venn_ece / raw_ece) * 100, 1)
                                if raw_ece > 0 else None),
            "raw_brier": round(float(np.mean((ev_s - ev_y) ** 2)), 4),
            "venn_brier": round(float(np.mean((merged - ev_y) ** 2)), 4),
            "bins_used": n_bins,
            "estimates_reliable": reliable,
            "note": ("" if reliable else
                     f"Only {n_events} calibration events. Intervals are still "
                     f"produced, but every figure here rests on too few events "
                     f"to be trusted. Do not quote these."),
        }
        report.append(row)

        flag = "" if reliable else "   <- too few events, do not quote"
        imp = row["improvement_pct"]
        imp_txt = f"({imp:+.0f}%)" if imp is not None else "(n/a)"
        print(f"{outcome:18s} cal_ev={n_events:5d}  "
              f"cal.err {raw_ece:.4f} -> {venn_ece:.4f} {imp_txt:>8s}  "
              f"brier {row['raw_brier']:.4f} -> {row['venn_brier']:.4f}{flag}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "venn_abers.json").write_text(json.dumps(artifacts))
    (OUT / "conformal_report.json").write_text(json.dumps(report, indent=2))

    print(f"\nWrote {ARTIFACTS / 'venn_abers.json'}")
    print(f"Wrote {OUT / 'conformal_report.json'}")
    print("\nReading the output: interval width reflects how much CALIBRATION "
          "DATA sits near that score, not how good the model is. A weak model "
          "produces narrow intervals, not wide ones — see the header of this "
          "file. Judge model quality by AUC and actionability tier instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
