# IST-1 Data Quality Report

## 1. Load

- Rows: **19,435** (expected 19,435)
- Columns: **112** (expected 112)

## 2. Dictionary reconciliation

- `RTIME`: listed in dictionary, absent from CSV — currently **ABSENT**
- `FMETHOD`: listed in dictionary, absent from CSV — currently **ABSENT**
- `FSOURCE`: listed in dictionary, absent from CSV — currently **ABSENT**
- `SETASPLT`: listed in dictionary, absent from CSV — currently **ABSENT**
- `ID`: appears in CSV as DIED — currently **ABSENT**
- `STR14`: appears in CSV as STRK14 — currently **ABSENT**
- `MI14`: listed in dictionary, absent from CSV — currently **ABSENT**
- `TICH`: listed in dictionary, absent from CSV — currently **ABSENT**
- `TMAJH`: listed in dictionary, absent from CSV — currently **ABSENT**

> None of the absent fields are needed for our five outcomes.

## 3. Corrections applied

- **RXHEP**: merged `H` → `M` (245 rows; 245 of them in the pilot phase, consistent with the dictionary note).
- **FAP**: normalised lowercase `n` → `N` (1 row).
- **RATRIAL**: 984 missing, all in the pilot phase. This is missing *by design*, not at random — encoded as its own `unknown` category rather than imputed.

## 4. Feature set

- 17 source columns → 17 model features
- Deficits use `C` (can't assess) → missing, kept distinct from `N` (absent)

| Feature | Missing % |
|---|---|
| `RDEF1` | 1.3% |
| `RDEF2` | 0.6% |
| `RDEF3` | 1.3% |
| `RDEF4` | 3.0% |
| `RDEF5` | 20.3% |
| `RDEF6` | 17.7% |
| `RDEF7` | 8.2% |
| `RDEF8` | 6.4% |

## 5. Outcome definitions

The dictionary distinguishes raw discharge-form fields from derived `*14` indicators that are properly windowed to 14 days. We use the indicators. Differences are material — see the table.

| Outcome | Column used | Instead of | Events | Note |
|---|---|---|---|---|
| 14-day death | `ID14` | `DDEAD` (2,034) | 1,781 | DDEAD includes deaths after day 14 |
| 14-day recurrence | `ISC14` | `DRSISC` (413) | 395 | properly windowed |
| 14-day cerebral bleed | `H14` | `DRSH` (97) | 161 | wider analysis definition |
| 14-day PE | `PE14` | `DPE` (125) | 134 | properly windowed |
| 6-month poor outcome | `OCCODE` 1,2 vs 3,4 | — | 12,125 | 150 rows excluded (codes 0, 9) |

## 6. Events per variable (EPV)

EPV counts predictor **parameters**, not columns: a 3-way categorical contributes k−1 parameters once encoded.

- Columns: 17
- **Predictor parameters: 22**
  - `STYPE`: 5 levels → 4 parameters
  - `RATRIAL_cat`: 3 levels → 2 parameters
  - `RXHEP`: 3 levels → 2 parameters

Conventional floor is 10 events per parameter; ~20 is advised for a reliable c-statistic (Austin & Steyerberg 2017).

| Outcome | Events | EPV | Verdict |
|---|---|---|---|
| `death_14d` | 1,781 | 81.0 | comfortable |
| `recurrence_14d` | 395 | 18.0 | adequate — report wide CIs |
| `haemorrhage_14d` | 161 | 7.3 | **UNDERPOWERED at full feature set** |
| `pe_14d` | 134 | 6.1 | **UNDERPOWERED at full feature set** |
| `poor_outcome_6m` | 12,125 | 551.1 | comfortable |

### Action required

Two outcomes cannot support the full feature set. Do **not** train them on all 22 parameters and report the AUC as if it were sound. Options, in order of preference:

1. **Reduced feature set for rare outcomes.** Use a core clinical set and drop the high-missingness deficits (`RDEF5`–`RDEF8`, 6–20% missing).
2. **Penalised regression** (ridge/lasso) — tolerates lower EPV by shrinking coefficients, at the cost of some interpretability.
3. **Disclose** in the model card and pitch. A stated limitation is a strength; a hidden one is a finding waiting for a judge.

Budget at the core feature set:

Core set = ['AGE', 'SEX', 'RDELAY', 'RSBP', 'RCONSC_ord', 'RATRIAL_cat', 'STYPE', 'RDEF2', 'RDEF4']
→ **13 parameters**

| Outcome | Events | EPV (full) | EPV (core) |
|---|---|---|---|
| `haemorrhage_14d` | 161 | 7.3 | **12.4** |
| `pe_14d` | 134 | 6.1 | **10.3** |

## 7. Output

- `ml/outputs/ist1_clean.csv` — 19,435 rows × 22 columns (17 features + 5 outcomes)