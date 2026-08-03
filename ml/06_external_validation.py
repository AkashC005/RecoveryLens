"""
RecoveryLens — ml/06_external_validation.py
===========================================
External validation of the frozen IST-1 models on IST-3.

Run:  python ml/06_external_validation.py

WHAT THIS DOES NOT DO
---------------------
It never refits. Models are loaded from models/final_*.pkl exactly as deployed,
used to predict, and scored. Refitting on IST-3 would make this a second training
run wearing the word "validation".

READ THIS BEFORE TRUSTING ANY NUMBER IT PRINTS
----------------------------------------------
Several IST-3 codings below were INFERRED from distributions, not read from the
IST-3 data dictionary. Each is registered in ASSUMPTIONS with verified=False.
While any remain unverified the report is stamped PROVISIONAL, because a single
wrong coding silently corrupts every metric.

The clearest example: IST-3 `gender` is 1/2 with no obvious direction. Mean age
is 80.0 for gender=1 and 74.4 for gender=2. In IST-1 women are the older group
(74.2 vs 69.6), so gender=1 is inferred to be FEMALE — the opposite of the
natural "1 = male" guess. Get this backwards and the sex feature is inverted for
all 3,035 patients while every number still looks plausible.

Get the dictionary from the IST-3 release on Edinburgh DataShare, confirm each
entry, flip verified=True, and re-run.

STRUCTURAL LIMITS FOUND DURING MAPPING
--------------------------------------
Four of the 24 model features do not exist in IST-3, and they are not minor:

  RXASP  IST-1 randomised aspirin. IST-3 randomised rt-PA vs placebo.
  RXHEP  IST-1 randomised heparin. Same.
  RCT    IST-3 imaged everyone before randomisation, so it is constant.
  RSLEEP No symptoms-on-waking variable exists.

From outputs/final_metrics.json, RCT is the #1 SHAP driver for recurrence_14d,
RXASP #2, RXHEP #3; RXHEP is #2 for haemorrhage; RCT is #4 for poor_outcome_6m
and nonadherence_6m. The models therefore lean heavily on IST-1 trial-design
artefacts. That is the finding, not a nuisance to be engineered away.

Two outcomes cannot be validated at all:
  pe_14d          IST-3 records no pulmonary embolism variable.
  recurrence_14d  `rec7` is a form-received flag (3,034 of 3,035 patients), not
                  a recurrence event. No usable equivalent exists.

Two more have a shortened horizon and are reported as such, never as 14-day:
  haemorrhage_14d -> sich7 is symptomatic ICH at 7 days. Stricter definition than
                     IST-1's H14, and in a thrombolysis trial largely
                     treatment-induced. Expect this to transport badly.
  death_14d       -> derived from survival time; see DEATH_14D below.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
IST3 = ROOT / "ml" / "data" / "IST3_clean.csv"
MODELS = ROOT / "models"
OUT = ROOT / "outputs"
SEED = 42
N_BOOTSTRAP = 1000

# --------------------------------------------------------------------------- #
# Assumptions register
# --------------------------------------------------------------------------- #
# Every inferred coding lives here. Nothing is hidden in the mapping code.
ASSUMPTIONS: dict[str, dict] = {
    "gender": {
        "verified": False,
        "claim": "IST-3 gender 1=female, 2=male",
        "basis": "Mean age 80.0 (gender=1) vs 74.4 (gender=2). IST-1 women are "
                 "older (74.2 vs 69.6). Same direction implies 1=female.",
        "risk": "If inverted, the SEX feature is wrong for all 3,035 rows and "
                "every metric shifts without looking broken.",
    },
    "deficits": {
        "verified": False,
        "claim": "Deficit variables 1=present, 2=absent, 4=cannot assess",
        "basis": "weakarm_rand is 1 for 2,622 of 3,035 — arm weakness is the "
                 "commonest deficit, so 1 must be 'present'. IST-1 RDEF2 is "
                 "likewise Y for 16,645 of 19,435. Value 4 is rare (10 rows), "
                 "consistent with IST-1's 'C' (cannot assess, 123 rows).",
        "risk": "Inversion would flip all eight deficit features and both "
                "derived counts.",
    },
    "stroketype": {
        "verified": False,
        "claim": "stroketype 1=PACS, 2=TACS, 3=LACS, 4=POCS, 5=OTH",
        "basis": "WEAKEST ASSUMPTION IN THIS FILE. Frequency-ordered only, and "
                 "the IST-1 frequency order (PACS>LACS>TACS>POCS) does NOT match "
                 "IST-3's (1>2>3>4). IST-3 enrolled more severe strokes, so the "
                 "true order is likely different. Do not accept without the "
                 "dictionary.",
        "risk": "Mis-ordering scrambles a categorical feature that one-hot "
                "encodes into four columns.",
    },
    "atrialfib": {
        "verified": False,
        "claim": "atrialfib_rand 1=yes, 2=no",
        "basis": "914 of 3,035 (30%) coded 1. AF prevalence in an elderly "
                 "thrombolysis cohort is plausibly ~30%; 70% would not be.",
        "risk": "Inversion flips a categorical feature.",
    },
    "infarct": {
        "verified": False,
        "claim": "infarct 0=not visible, 1 or 2=visible",
        "basis": "Three levels (0:1792, 1:701, 2:542) against IST-1's binary "
                 "RVISINF. Collapsing 1 and 2 to 'visible' is a guess; 2 may "
                 "mean 'cannot assess'.",
        "risk": "Would mislabel 542 patients on RVISINF.",
    },
    "aspirin_pre": {
        "verified": False,
        "claim": "aspirin_pre 1=yes, 2=no; 20 and 40 are missing codes",
        "basis": "Values 20 and 40 appear across many IST-3 columns in patterns "
                 "consistent with missing-data sentinels, not real values.",
        "risk": "Treating 40 as a real value would corrupt RASP3.",
    },
    "meds_6m": {
        "verified": False,
        "claim": "aspirin6 / bloodthin6: 1=yes, 2=no, 3=unknown; 10/20/30/40 missing",
        "basis": "Same sentinel pattern. 1,026 patients coded 3 and 815 coded 10 "
                 "- too many for either to be a real treatment state.",
        "risk": "Directly determines the nonadherence_6m denominator.",
    },
    "censor6": {
        "verified": False,
        "claim": "censor6 1=censored (alive), 0=event (died)",
        "basis": "surv6<=14 AND censor6==1 yields 8 patients, but dead7 is 270. "
                 "The indicator must therefore run the other way.",
        "risk": "Inversion makes death_14d meaningless.",
    },
}


def unverified() -> list[str]:
    return [k for k, v in ASSUMPTIONS.items() if not v["verified"]]


# --------------------------------------------------------------------------- #
# Feature mapping
# --------------------------------------------------------------------------- #
# Fixed values for features IST-3 does not contain. This frames the question as:
# "how does the model transport to a cohort where the trial-design variables do
# not apply?" - which is a legitimate question, but NOT the same as validating
# the model as deployed. Section 3 of the report quantifies how much of the
# model's signal rests on these.
ABSENT_FEATURE_DEFAULTS = {
    "RXASP": 0,      # no trial aspirin allocation
    "RXHEP": "N",    # no trial heparin allocation
    "RCT": 1,        # IST-3 imaged everyone pre-randomisation
    "RSLEEP": 0,     # modal IST-1 value; variable absent from IST-3
}

DEFICIT_MAP = {1.0: 1, 2.0: 0, 4.0: 2}          # present / absent / cannot assess
STROKETYPE_MAP = {1.0: "PACS", 2.0: "TACS", 3.0: "LACS", 4.0: "POCS", 5.0: "OTH"}
MISSING_SENTINELS = {10.0, 20.0, 30.0, 40.0}

DEFICIT_COLS = [
    ("RDEF1", "weakface_rand"), ("RDEF2", "weakarm_rand"),
    ("RDEF3", "weakleg_rand"), ("RDEF4", "dysphasia_rand"),
    ("RDEF5", "hemianopia_rand"), ("RDEF6", "visuospat_rand"),
    ("RDEF7", "brainstemsigns_rand"), ("RDEF8", "otherdeficit_rand"),
]

FEATURES = (
    ["AGE", "RDELAY", "RSBP", "DEFCOUNT", "DEFUNASSESSED"]
    + ["SEX", "RXASP", "RCONSC_ord"] + [f"RDEF{i}" for i in range(1, 9)]
    + ["RSLEEP", "RCT", "RVISINF", "RHEP24", "RASP3"]
    + ["STYPE", "RATRIAL_cat", "RXHEP"]
)


def _sentinel_to_nan(s: pd.Series) -> pd.Series:
    return s.where(~s.isin(MISSING_SENTINELS))


def build_features(d: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=d.index)

    X["AGE"] = d["age"]
    X["RDELAY"] = d["randdelay"]          # hours in both trials
    X["RSBP"] = d["sbprand"]

    # See ASSUMPTIONS["gender"] - inferred as 1=female, so male is gender==2.
    X["SEX"] = (d["gender"] == 2).astype(int)

    # IST-1 RCONSC: F(ully alert)=0, D(rowsy)=1, U(nconscious)=2.
    # IST-3 has no equivalent categorical, so derive from GCS. Thresholds are the
    # conventional ones; the mapping is approximate by construction and is itself
    # a source of transport error.
    gcs = d["gcs_score_rand"]
    X["RCONSC_ord"] = np.select([gcs >= 15, gcs >= 9], [0, 1], default=2)

    for ist1, ist3 in DEFICIT_COLS:
        X[ist1] = d[ist3].map(DEFICIT_MAP).fillna(0).astype(int)

    X["RVISINF"] = (d["infarct"] > 0).astype(int)
    X["RASP3"] = (_sentinel_to_nan(d["aspirin_pre"]) == 1).astype(int)

    # IST-1 RHEP24 = any heparin in the 24h before randomisation.
    hep = pd.concat([
        _sentinel_to_nan(d["lowdose_heparin_pre"]) == 1,
        _sentinel_to_nan(d["fulldose_heparin_pre"]) == 1,
    ], axis=1).any(axis=1)
    X["RHEP24"] = hep.astype(int)

    X["STYPE"] = d["stroketype"].map(STROKETYPE_MAP).fillna("OTH")
    X["RATRIAL_cat"] = np.where(d["atrialfib_rand"] == 1, "Y", "N")

    for col, val in ABSENT_FEATURE_DEFAULTS.items():
        X[col] = val

    deficits = [c for c, _ in DEFICIT_COLS]
    X["DEFCOUNT"] = X[deficits].clip(upper=1).sum(axis=1)
    X["DEFUNASSESSED"] = (X[deficits] == 2).sum(axis=1)

    return X[FEATURES]


# --------------------------------------------------------------------------- #
# Outcome derivation
# --------------------------------------------------------------------------- #
UNVALIDATABLE = {
    "pe_14d": "IST-3 records no pulmonary embolism variable. Nothing to score "
              "against.",
    "recurrence_14d": "`rec7` is a form-received flag (3,034 of 3,035 patients), "
                      "not a recurrence event. No usable equivalent exists.",
}


def build_outcomes(d: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    y = pd.DataFrame(index=d.index)
    notes: dict[str, str] = {}

    # censor6 inferred as 1=censored/alive, 0=event. See ASSUMPTIONS["censor6"].
    died = d["censor6"] == 0
    y["death_14d"] = (died & (d["surv6"] <= 14)).astype(float)
    notes["death_14d"] = ("Derived from surv6 <= 14 with censor6 as the event "
                          "indicator. Depends on the censor6 assumption.")

    y["haemorrhage_14d"] = d["sich7"].astype(float)
    notes["haemorrhage_14d"] = (
        "sich7 = SYMPTOMATIC intracranial haemorrhage at 7 DAYS. Two departures "
        "from IST-1's H14: a 7-day rather than 14-day horizon, and a stricter "
        "symptomatic-only definition. In a thrombolysis trial these events are "
        "largely treatment-induced, which IST-1 could not observe. Poor "
        "transport here is expected and is not evidence the model is broken.")

    y["poor_outcome_6m"] = (d["deadordep6"] == 1).astype(float)
    notes["poor_outcome_6m"] = ("deadordep6 maps directly onto IST-1's OCCODE "
                                "dead-or-dependent definition. Cleanest of the "
                                "five.")

    asp = _sentinel_to_nan(d["aspirin6"])
    thin = _sentinel_to_nan(d["bloodthin6"])
    known = asp.isin([1, 2]) & thin.isin([1, 2])
    alive = d["dead6mo"] == 0
    on_neither = (asp == 2) & (thin == 2)
    y["nonadherence_6m"] = np.where(alive & known, on_neither.astype(float), np.nan)
    notes["nonadherence_6m"] = ("Alive at 6 months, both medication fields known, "
                                "on neither antiplatelet nor anticoagulant - "
                                "mirrors the IST-1 FAP/FOAC definition.")

    return y, notes


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def bootstrap_auc_ci(y, p, n=N_BOOTSTRAP, seed=SEED):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(y))
    aucs = []
    for _ in range(n):
        s = rng.choice(idx, len(idx), replace=True)
        if len(np.unique(y[s])) < 2:
            continue
        aucs.append(roc_auc_score(y[s], p[s]))
    return (float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))) \
        if aucs else (float("nan"), float("nan"))


def calibration(y, p):
    """Slope and calibration-in-the-large from a logit-link recalibration."""
    import statsmodels.api as sm
    eps = 1e-6
    lp = np.log(np.clip(p, eps, 1 - eps) / (1 - np.clip(p, eps, 1 - eps)))
    slope = float(sm.Logit(y, sm.add_constant(lp)).fit(disp=0).params[1])
    citl = float(sm.Logit(y, np.ones_like(y), offset=lp).fit(disp=0).params[0])
    return slope, citl


def evaluate(name, model, X, y):
    mask = y.notna()
    Xm, ym = X[mask], y[mask].to_numpy()
    if len(np.unique(ym)) < 2:
        return {"outcome": name, "error": "single-class outcome after masking"}

    p = model.predict_proba(Xm)[:, 1]
    lo, hi = bootstrap_auc_ci(ym, p)
    try:
        slope, citl = calibration(ym, p)
    except Exception as exc:
        slope, citl = float("nan"), float("nan")
        print(f"  calibration failed for {name}: {type(exc).__name__}")

    return {
        "outcome": name, "n": int(mask.sum()), "events": int(ym.sum()),
        "prevalence": round(float(ym.mean()), 4),
        "auc": round(float(roc_auc_score(ym, p)), 4), "ci": [round(lo, 4), round(hi, 4)],
        "brier": round(float(brier_score_loss(ym, p)), 4),
        "cal_slope": round(slope, 4), "citl": round(citl, 4),
    }


# --------------------------------------------------------------------------- #
def main() -> int:
    if not IST3.exists():
        print(f"Missing {IST3}", file=sys.stderr)
        return 1

    pending = unverified()
    if pending:
        print("=" * 72)
        print("PROVISIONAL RESULTS — unverified codings:", ", ".join(pending))
        print("Check each against the IST-3 data dictionary before quoting these")
        print("numbers anywhere. See the ASSUMPTIONS block in this file.")
        print("=" * 72, "\n")

    d = pd.read_csv(IST3, low_memory=False)
    print(f"IST-3: {len(d):,} patients\n")

    X = build_features(d)
    y, notes = build_outcomes(d)

    internal = json.loads((OUT / "final_metrics.json").read_text())["metrics"]

    rows = []
    for outcome in ["death_14d", "haemorrhage_14d", "poor_outcome_6m", "nonadherence_6m"]:
        path = MODELS / f"final_{outcome}.pkl"
        if not path.exists():
            print(f"  missing model: {path}")
            continue
        res = evaluate(outcome, joblib.load(path), X, y[outcome])
        res["ist1_auc"] = internal.get(outcome, {}).get("auc")
        res["note"] = notes.get(outcome, "")
        rows.append(res)
        if "error" in res:
            print(f"{outcome:18s} {res['error']}")
        else:
            delta = res["auc"] - (res["ist1_auc"] or 0)
            print(f"{outcome:18s} n={res['n']:5,} ev={res['events']:5,} "
                  f"AUC {res['auc']:.3f} [{res['ci'][0]:.3f},{res['ci'][1]:.3f}] "
                  f"(IST-1 {res['ist1_auc']:.3f}, {delta:+.3f})  "
                  f"slope={res['cal_slope']:.2f} CITL={res['citl']:+.2f}")

    for outcome, why in UNVALIDATABLE.items():
        print(f"{outcome:18s} NOT VALIDATABLE — {why}")

    payload = {
        "provisional": bool(pending),
        "unverified_assumptions": {k: ASSUMPTIONS[k] for k in pending},
        "n_patients": len(d),
        "absent_features": ABSENT_FEATURE_DEFAULTS,
        "unvalidatable_outcomes": UNVALIDATABLE,
        "results": rows,
    }
    (OUT / "external_validation_ist3.json").write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT / 'external_validation_ist3.json'}")

    if pending:
        print("\nStill PROVISIONAL. Verify the codings, set verified=True, re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
