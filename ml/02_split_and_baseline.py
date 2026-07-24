"""
RecoveryLens — ml/02_split_and_baseline.py
==========================================
Day 2. Creates the single train/test split that every later model must reuse,
then fits a penalised logistic regression baseline per outcome.

This baseline is the floor. In Day 4's benchmark, any gradient-boosted model
that does not beat it outside the confidence interval has not earned its
loss of interpretability.

Outputs
-------
  outputs/split_indices.csv     row -> train/test, reused by every later script
  outputs/baseline_results.md   report
  models/baseline_<outcome>.pkl

Run
---
  python ml/02_split_and_baseline.py
"""

from pathlib import Path
import warnings

import numpy as np
import pandas as pd
import joblib
import statsmodels.api as sm
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore", category=FutureWarning)

IN = Path("ml/outputs/ist1_clean.csv")
OUT = Path("ml/outputs")
MODELS = Path("ml/models")
MODELS.mkdir(parents=True, exist_ok=True)

SEED = 42
N_BOOTSTRAP = 1000

report: list[str] = []


def log(line: str = "") -> None:
    print(line)
    report.append(line)


# ---------------------------------------------------------------------------
# Feature sets
# ---------------------------------------------------------------------------
NUMERIC = ["AGE", "RDELAY", "RSBP"]
BINARY_ORDINAL = ["SEX", "RXASP", "RCONSC_ord"] + [f"RDEF{i}" for i in range(1, 9)]
CATEGORICAL = ["STYPE", "RATRIAL_cat", "RXHEP"]

FULL = NUMERIC + BINARY_ORDINAL + CATEGORICAL

# Reduced set for the two outcomes that cannot support 22 parameters.
# Keeps the strongest clinical predictors and the two best-populated deficits
# (arm/hand, dysphasia); drops the high-"can't assess" items.
CORE = ["AGE", "SEX", "RDELAY", "RSBP", "RCONSC_ord",
        "RATRIAL_cat", "STYPE", "RDEF2", "RDEF4"]

OUTCOMES = {
    "death_14d":       FULL,
    "recurrence_14d":  FULL,
    "haemorrhage_14d": CORE,   # 161 events — EPV 7.3 at full, 12.4 at core
    "pe_14d":          CORE,   # 134 events — EPV 6.1 at full, 10.3 at core
    "poor_outcome_6m": FULL,
}


def build_pipeline(features: list[str]) -> Pipeline:
    """Preprocessing + penalised logistic regression, fitted inside CV folds."""
    num = [c for c in features if c in NUMERIC]
    cat = [c for c in features if c in CATEGORICAL]
    rest = [c for c in features if c in BINARY_ORDINAL]

    pre = ColumnTransformer(
        [
            ("num", StandardScaler(), num),
            ("cat", OneHotEncoder(drop="first", handle_unknown="ignore"), cat),
            ("pass", "passthrough", rest),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            ("clf", LogisticRegression(penalty="l2", solver="lbfgs",
                                       max_iter=3000, random_state=SEED)),
        ]
    )


def bootstrap_auc_ci(y_true, y_prob, n=N_BOOTSTRAP, seed=SEED):
    """Percentile bootstrap 95% CI for AUC. An AUC without an interval is not a result."""
    rng = np.random.default_rng(seed)
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    aucs = []
    for _ in range(n):
        idx = rng.integers(0, len(y_true), len(y_true))
        if len(np.unique(y_true[idx])) < 2:      # resample lost a class
            continue
        aucs.append(roc_auc_score(y_true[idx], y_prob[idx]))
    return float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))


def calibration_metrics(y_true, y_prob):
    """
    Calibration slope and calibration-in-the-large (CITL).

    Slope: regress outcome on the linear predictor. 1.0 = ideal;
           <1 means predictions are too extreme (typical overfitting).
    CITL:  intercept with the linear predictor as a fixed offset.
           0 = ideal; <0 means the model over-predicts risk overall.
    """
    eps = 1e-15
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    lp = np.log(np.clip(y_prob, eps, 1 - eps) / np.clip(1 - y_prob, eps, 1 - eps))

    slope_fit = sm.GLM(y_true, sm.add_constant(lp),
                       family=sm.families.Binomial()).fit()
    slope = float(np.asarray(slope_fit.params)[1])

    citl_fit = sm.GLM(y_true, np.ones((len(y_true), 1)),
                      family=sm.families.Binomial(), offset=lp).fit()
    citl = float(np.asarray(citl_fit.params)[0])

    return slope, citl


# ---------------------------------------------------------------------------
# 1. Load and split
# ---------------------------------------------------------------------------
log("# Baseline Results — Penalised Logistic Regression")
log()
log("## 1. Train/test split")
log()

df = pd.read_csv(IN)
log(f"- Loaded {len(df):,} rows")

# Stratify on the rarest outcome so both halves keep enough rare events.
# Every later model must reuse this exact split — otherwise the benchmark
# compares models across different data and the comparison means nothing.
strat = df["pe_14d"]
train_idx, test_idx = train_test_split(
    df.index, test_size=0.2, random_state=SEED, stratify=strat
)

split = pd.Series("train", index=df.index, name="split")
split.loc[test_idx] = "test"
split.to_csv(OUT / "split_indices.csv", index_label="row")

log(f"- Train: **{len(train_idx):,}** · Test: **{len(test_idx):,}** (80/20, seed {SEED})")
log(f"- Stratified on `pe_14d` (rarest outcome)")
log()
log("Event counts per split — check the rare outcomes have enough in both halves:")
log()
log("| Outcome | Train events | Test events |")
log("|---|---|---|")
for name in OUTCOMES:
    tr = int(df.loc[train_idx, name].sum())
    te = int(df.loc[test_idx, name].sum())
    log(f"| `{name}` | {tr:,} | {te:,} |")
log()

# ---------------------------------------------------------------------------
# 2. Fit baselines
# ---------------------------------------------------------------------------
log("## 2. Baseline performance (held-out test set)")
log()
log("Regularisation strength `C` tuned by 5-fold CV **inside the training set "
    "only**. Models are fitted unweighted: class weighting shifts the intercept "
    "and degrades calibration, which matters more here than a marginal AUC gain.")
log()
log("| Outcome | Features | Params | Test AUC (95% CI) | Brier | Cal. slope | CITL |")
log("|---|---|---|---|---|---|---|")

results = {}

for name, features in OUTCOMES.items():
    sub = df[df[name].notna()]           # poor_outcome_6m has excluded rows
    tr = sub.index.intersection(train_idx)
    te = sub.index.intersection(test_idx)

    X_tr, y_tr = sub.loc[tr, features], sub.loc[tr, name].astype(int)
    X_te, y_te = sub.loc[te, features], sub.loc[te, name].astype(int)

    grid = GridSearchCV(
        build_pipeline(features),
        {"clf__C": [0.01, 0.1, 1.0, 10.0]},
        scoring="roc_auc",
        cv=StratifiedKFold(5, shuffle=True, random_state=SEED),
        n_jobs=-1,
    )
    grid.fit(X_tr, y_tr)
    model = grid.best_estimator_

    prob = model.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, prob)
    lo, hi = bootstrap_auc_ci(y_te, prob)
    brier = brier_score_loss(y_te, prob)
    slope, citl = calibration_metrics(y_te, prob)

    n_params = model.named_steps["pre"].transform(X_tr[:1]).shape[1]
    tag = "core" if features is CORE else "full"

    log(f"| `{name}` | {tag} | {n_params} | "
        f"{auc:.3f} ({lo:.3f}–{hi:.3f}) | {brier:.4f} | {slope:.2f} | {citl:+.3f} |")

    joblib.dump(model, MODELS / f"baseline_{name}.pkl")
    results[name] = dict(auc=auc, ci=(lo, hi), brier=brier,
                         slope=slope, citl=citl, C=grid.best_params_["clf__C"])

log()
log("**Reading the calibration columns.** Slope 1.0 and CITL 0.0 are ideal. "
    "Slope below 1 means predictions are too extreme; CITL below 0 means the "
    "model over-predicts risk overall. Both are correctable by recalibration — "
    "and both are the metrics that will drift when this model meets "
    "modern-era data, which is exactly the argument already made about IST-1's "
    "vintage.")
log()

# ---------------------------------------------------------------------------
# 3. Chosen hyperparameters
# ---------------------------------------------------------------------------
log("## 3. Selected regularisation")
log()
log("| Outcome | C |")
log("|---|---|")
for name, r in results.items():
    log(f"| `{name}` | {r['C']} |")
log()
log(f"Models saved to `{MODELS}/`. Split saved to `{OUT / 'split_indices.csv'}` — "
    f"**every later script must load this split, not create its own.**")

(OUT / "baseline_results.md").write_text("\n".join(report), encoding="utf-8")
print(f"\nReport written to {OUT / 'baseline_results.md'}")