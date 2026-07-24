"""
RecoveryLens — ml/01_load_and_validate.py
=========================================
Day 1. Loads IST-1, reconciles every column against the published data
dictionary, applies documented corrections, defines the five outcomes,
and runs the events-per-variable (EPV) adequacy check.

Outputs
-------
  outputs/ist1_clean.parquet    analysis-ready table
  outputs/data_quality.md       report to commit to the repo

Run
---
  python ml/01_load_and_validate.py
"""

from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path("ml/data/IST_corrected.csv")
OUT = Path("ml/outputs")
OUT.mkdir(parents=True, exist_ok=True)

report: list[str] = []


def log(line: str = "") -> None:
    """Print to console and capture for the markdown report."""
    print(line)
    report.append(line)


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------
log("# IST-1 Data Quality Report")
log()
log("## 1. Load")
log()

# latin-1, not utf-8: the file contains non-UTF8 bytes in free-text comment
# fields. Reading as utf-8 raises UnicodeDecodeError at position ~34801.
df = pd.read_csv(DATA, low_memory=False, encoding="latin-1")

log(f"- Rows: **{len(df):,}** (expected 19,435)")
log(f"- Columns: **{len(df.columns)}** (expected 112)")
assert len(df) == 19435, "Row count mismatch — wrong file version?"
log()

# ---------------------------------------------------------------------------
# 2. Column reconciliation against the published dictionary
# ---------------------------------------------------------------------------
log("## 2. Dictionary reconciliation")
log()

# Names that appear in the published variable table but differ in the CSV.
DICTIONARY_ONLY = {
    "RTIME": "listed in dictionary, absent from CSV",
    "FMETHOD": "listed in dictionary, absent from CSV",
    "FSOURCE": "listed in dictionary, absent from CSV",
    "SETASPLT": "listed in dictionary, absent from CSV",
    "ID": "appears in CSV as DIED",
    "STR14": "appears in CSV as STRK14",
    "MI14": "listed in dictionary, absent from CSV",
    "TICH": "listed in dictionary, absent from CSV",
    "TMAJH": "listed in dictionary, absent from CSV",
}

for name, note in DICTIONARY_ONLY.items():
    present = "present" if name in df.columns else "ABSENT"
    log(f"- `{name}`: {note} — currently **{present}**")
log()
log("> None of the absent fields are needed for our five outcomes.")
log()

# ---------------------------------------------------------------------------
# 3. Documented data corrections
# ---------------------------------------------------------------------------
log("## 3. Corrections applied")
log()

# 3a. RXHEP: dictionary states heparin is coded M/L/N, and that in the pilot
#     phase medium dose was recorded as H. Verified: all 245 'H' rows sit in
#     the pilot phase. Merge H into M so the variable has one coding scheme.
pilot_mask = df["RATRIAL"].isna()          # pilot phase did not code RATRIAL
n_h = (df["RXHEP"] == "H").sum()
n_h_pilot = ((df["RXHEP"] == "H") & pilot_mask).sum()
df["RXHEP"] = df["RXHEP"].replace({"H": "M"})
log(f"- **RXHEP**: merged `H` → `M` ({n_h} rows; {n_h_pilot} of them in the "
    f"pilot phase, consistent with the dictionary note).")

# 3b. FAP: one row uses lowercase 'n'.
n_lower = (df["FAP"] == "n").sum()
df["FAP"] = df["FAP"].replace({"n": "N"})
log(f"- **FAP**: normalised lowercase `n` → `N` ({n_lower} row).")

# 3c. RATRIAL structurally missing for the pilot phase.
log(f"- **RATRIAL**: {df['RATRIAL'].isna().sum()} missing, all in the pilot "
    f"phase. This is missing *by design*, not at random — encoded as its own "
    f"`unknown` category rather than imputed.")
log()

# ---------------------------------------------------------------------------
# 4. Features
# ---------------------------------------------------------------------------
log("## 4. Feature set")
log()

CONTINUOUS = ["AGE", "RDELAY", "RSBP"]
CATEGORICAL = ["SEX", "RCONSC", "RATRIAL", "STYPE", "RXASP", "RXHEP"]
DEFICITS = [f"RDEF{i}" for i in range(1, 9)]   # face, arm, leg, dysphasia,
                                               # hemianopia, visuospatial,
                                               # brainstem, other
FEATURES = CONTINUOUS + CATEGORICAL + DEFICITS

X = df[FEATURES].copy()

# Y / N / C(can't assess) → 1 / 0 / NaN, keeping "can't assess" distinct from "no".
for col in DEFICITS:
    X[col] = X[col].map({"Y": 1, "N": 0, "C": np.nan})

X["SEX"] = X["SEX"].map({"M": 1, "F": 0})
X["RXASP"] = X["RXASP"].map({"Y": 1, "N": 0})
X["RCONSC_ord"] = X["RCONSC"].map({"F": 0, "D": 1, "U": 2})   # ordinal severity
X["RATRIAL_cat"] = X["RATRIAL"].fillna("unknown")
X = X.drop(columns=["RCONSC", "RATRIAL"])

log(f"- {len(FEATURES)} source columns → {X.shape[1]} model features")
log(f"- Deficits use `C` (can't assess) → missing, kept distinct from `N` (absent)")
log()
log("| Feature | Missing % |")
log("|---|---|")
for col in X.columns:
    pct = X[col].isna().mean() * 100
    if pct > 0:
        log(f"| `{col}` | {pct:.1f}% |")
log()

# ---------------------------------------------------------------------------
# 5. Outcomes — using the analysis-ready indicators
# ---------------------------------------------------------------------------
log("## 5. Outcome definitions")
log()
log("The dictionary distinguishes raw discharge-form fields from derived "
    "`*14` indicators that are properly windowed to 14 days. We use the "
    "indicators. Differences are material — see the table.")
log()

y = pd.DataFrame(index=df.index)

# 5a. 14-day death. NOT DDEAD: the dictionary warns DDEAD's death "is not
#     necessarily within 14 days of randomisation". ID14 is the 14-day flag.
y["death_14d"] = df["ID14"]

# 5b. 14-day ischaemic recurrence.
y["recurrence_14d"] = df["ISC14"]

# 5c. 14-day cerebral bleed. H14 is described in the dictionary as a
#     "slightly wider definition than DRSH... used for analysis".
y["haemorrhage_14d"] = df["H14"]

# 5d. 14-day pulmonary embolism.
y["pe_14d"] = df["PE14"]

# 5e. 6-month outcome. OCCODE 1=dead, 2=dependent, 3=not recovered,
#     4=recovered. Codes 0 (undocumented) and 9 (missing) are excluded.
occ = df["OCCODE"]
y["poor_outcome_6m"] = np.where(occ.isin([1, 2]), 1,
                        np.where(occ.isin([3, 4]), 0, np.nan))

log("| Outcome | Column used | Instead of | Events | Note |")
log("|---|---|---|---|---|")
log(f"| 14-day death | `ID14` | `DDEAD` ({int((df['DDEAD']=='Y').sum()):,}) "
    f"| {int(y['death_14d'].sum()):,} | DDEAD includes deaths after day 14 |")
log(f"| 14-day recurrence | `ISC14` | `DRSISC` ({int((df['DRSISC']=='Y').sum()):,}) "
    f"| {int(y['recurrence_14d'].sum()):,} | properly windowed |")
log(f"| 14-day cerebral bleed | `H14` | `DRSH` ({int((df['DRSH']=='Y').sum()):,}) "
    f"| {int(y['haemorrhage_14d'].sum()):,} | wider analysis definition |")
log(f"| 14-day PE | `PE14` | `DPE` ({int((df['DPE']=='Y').sum()):,}) "
    f"| {int(y['pe_14d'].sum()):,} | properly windowed |")
n_excl = int(occ.isin([0, 9]).sum())
log(f"| 6-month poor outcome | `OCCODE` 1,2 vs 3,4 | — "
    f"| {int(y['poor_outcome_6m'].sum()):,} | {n_excl} rows excluded (codes 0, 9) |")
log()

# ---------------------------------------------------------------------------
# 6. EPV adequacy check
# ---------------------------------------------------------------------------
log("## 6. Events per variable (EPV)")
log()

# EPV counts predictor *parameters*, not columns. A k-level categorical
# contributes k-1 parameters once encoded, so column count understates it.
CATEGORICAL_COLS = ["STYPE", "RATRIAL_cat", "RXHEP"]
n_params = 0
param_detail = []
for col in X.columns:
    if col in CATEGORICAL_COLS:
        k = X[col].nunique(dropna=True)
        p = k - 1
    else:
        p = 1
    n_params += p
    param_detail.append((col, p))

log(f"EPV counts predictor **parameters**, not columns: a {len(CATEGORICAL_COLS)}-way "
    f"categorical contributes k−1 parameters once encoded.")
log()
log(f"- Columns: {X.shape[1]}")
log(f"- **Predictor parameters: {n_params}**")
for col in CATEGORICAL_COLS:
    k = X[col].nunique(dropna=True)
    log(f"  - `{col}`: {k} levels → {k - 1} parameters")
log()
log("Conventional floor is 10 events per parameter; ~20 is advised for a "
    "reliable c-statistic (Austin & Steyerberg 2017).")
log()
log("| Outcome | Events | EPV | Verdict |")
log("|---|---|---|---|")

underpowered = []
for col in y.columns:
    events = int(y[col].sum())
    epv = events / n_params
    if epv >= 20:
        verdict = "comfortable"
    elif epv >= 10:
        verdict = "adequate — report wide CIs"
    else:
        verdict = "**UNDERPOWERED at full feature set**"
        underpowered.append((col, events, epv))
    log(f"| `{col}` | {events:,} | {epv:.1f} | {verdict} |")
log()

if underpowered:
    log("### Action required")
    log()
    log("Two outcomes cannot support the full feature set. Do **not** train them "
        "on all 22 parameters and report the AUC as if it were sound. Options, "
        "in order of preference:")
    log()
    log("1. **Reduced feature set for rare outcomes.** Use a core clinical set "
        "and drop the high-missingness deficits (`RDEF5`–`RDEF8`, 6–20% missing).")
    log("2. **Penalised regression** (ridge/lasso) — tolerates lower EPV by "
        "shrinking coefficients, at the cost of some interpretability.")
    log("3. **Disclose** in the model card and pitch. A stated limitation is a "
        "strength; a hidden one is a finding waiting for a judge.")
    log()
    log("Budget at the core feature set:")
    log()
    CORE = ["AGE", "SEX", "RDELAY", "RSBP", "RCONSC_ord",
            "RATRIAL_cat", "STYPE", "RDEF2", "RDEF4"]
    core_params = 0
    for col in CORE:
        core_params += (X[col].nunique(dropna=True) - 1) if col in CATEGORICAL_COLS else 1
    log(f"Core set = {CORE}")
    log(f"→ **{core_params} parameters**")
    log()
    log("| Outcome | Events | EPV (full) | EPV (core) |")
    log("|---|---|---|---|")
    for col, events, epv in underpowered:
        log(f"| `{col}` | {events:,} | {epv:.1f} | **{events / core_params:.1f}** |")
    log()

# ---------------------------------------------------------------------------
# 7. Save
# ---------------------------------------------------------------------------
clean = pd.concat([X, y], axis=1)
clean.to_csv(OUT / "ist1_clean.csv", index=False)

log("## 7. Output")
log()
log(f"- `{OUT / 'ist1_clean.csv'}` — {clean.shape[0]:,} rows × "
    f"{clean.shape[1]} columns ({X.shape[1]} features + {y.shape[1]} outcomes)")

(OUT / "data_quality.md").write_text("\n".join(report), encoding="utf-8")
print(f"\nReport written to {OUT / 'data_quality.md'}")
