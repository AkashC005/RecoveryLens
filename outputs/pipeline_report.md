# RecoveryLens — Pipeline Report

## 1. Load

- Rows: **19,435** (expected 19,435)
- Columns: **112** (expected 112)

## 2. Corrections applied

- **RXHEP**: merged `H` -> `M` (245 rows; 245 in pilot phase)
- **FAP**: lowercase `n` -> `N` (1 row)
- **RATRIAL**: 984 missing, all pilot phase -> `unknown` category

## 3. Features

- 24 features
- Missing values remaining: 0

## 4. Outcomes

| Outcome | Events | Usable rows |
|---|---|---|
| `death_14d` | 1,781 | 19,435 |
| `recurrence_14d` | 395 | 19,435 |
| `haemorrhage_14d` | 161 | 19,435 |
| `pe_14d` | 134 | 19,435 |
| `poor_outcome_6m` | 12,125 | 19,285 |
| `nonadherence_6m` | 2,416 | 14,022 |

## 5. EPV

- Columns: 24 -> **parameters: 29**

| Outcome | Events | EPV | Verdict |
|---|---|---|---|
| `death_14d` | 1,781 | 61.4 | comfortable |
| `recurrence_14d` | 395 | 13.6 | adequate — wide CIs |
| `haemorrhage_14d` | 161 | 5.6 | **low — see section 7** |
| `pe_14d` | 134 | 4.6 | **low — see section 7** |
| `poor_outcome_6m` | 12,125 | 418.1 | comfortable |
| `nonadherence_6m` | 2,416 | 83.3 | comfortable |

## 6. Split

- Train 15,548 / Test 3,887

| Outcome | Train events | Test events |
|---|---|---|
| `death_14d` | 1,430 | 351 |
| `recurrence_14d` | 313 | 82 |
| `haemorrhage_14d` | 120 | 41 |
| `pe_14d` | 107 | 27 |
| `poor_outcome_6m` | 9,679 | 2,446 |
| `nonadherence_6m` | 1,901 | 515 |

## 7. Feature-set comparison (training CV only)

| Outcome | CV AUC core | CV AUC full | Chosen |
|---|---|---|---|
| `death_14d` | 0.802 | 0.809 | full |
| `recurrence_14d` | 0.538 | 0.595 | full |
| `haemorrhage_14d` | 0.679 | 0.743 | full |
| `pe_14d` | 0.649 | 0.679 | full |
| `poor_outcome_6m` | 0.778 | 0.797 | full |
| `nonadherence_6m` | 0.573 | 0.623 | full |

## 8. Benchmark


**death_14d**

| Family | CV AUC | Test AUC (95% CI) | Brier | Slope | CITL |
|---|---|---|---|---|---|
| Logistic (L2) | 0.809 | 0.793 (0.769–0.818) | 0.0724 | 0.94 | -0.025 |
| CatBoost | 0.811 | 0.792 (0.768–0.818) | 0.0726 | 0.89 | -0.031 |
| Random Forest | 0.808 | 0.790 (0.767–0.816) | 0.0727 | 1.02 | -0.032 |
| XGBoost | nan | 0.790 (0.766–0.815) | 0.0732 | 0.85 | -0.029 |
| LightGBM | 0.802 | 0.786 (0.762–0.811) | 0.0741 | 0.78 | -0.020 |

> Logistic wins outright — ship the interpretable model.

**recurrence_14d**

| Family | CV AUC | Test AUC (95% CI) | Brier | Slope | CITL |
|---|---|---|---|---|---|
| Random Forest | 0.596 | 0.593 (0.533–0.650) | 0.0206 | 1.51 | +0.057 |
| Logistic (L2) | 0.595 | 0.590 (0.530–0.646) | 0.0206 | 0.69 | +0.051 |
| XGBoost | nan | 0.579 (0.517–0.639) | 0.0207 | 0.36 | +0.079 |
| CatBoost | 0.587 | 0.523 (0.457–0.584) | 0.0209 | 0.08 | +0.115 |
| LightGBM | 0.588 | 0.518 (0.454–0.581) | 0.0210 | 0.05 | +0.182 |

> Random Forest highest but inside the logistic CI (0.530–0.646) — not a real difference. Ship logistic.

**haemorrhage_14d**

| Family | CV AUC | Test AUC (95% CI) | Brier | Slope | CITL |
|---|---|---|---|---|---|
| Logistic (L2) | 0.743 | 0.732 (0.660–0.802) | 0.0104 | 0.91 | +0.330 |
| Random Forest | 0.730 | 0.713 (0.630–0.794) | 0.0104 | 1.24 | +0.333 |
| CatBoost | 0.713 | 0.713 (0.639–0.788) | 0.0104 | 0.75 | +0.414 |
| XGBoost | nan | 0.706 (0.632–0.782) | 0.0104 | 0.60 | +0.398 |
| LightGBM | 0.681 | 0.646 (0.566–0.728) | 0.0106 | 0.35 | +0.951 |

> Logistic wins outright — ship the interpretable model.

**pe_14d**

| Family | CV AUC | Test AUC (95% CI) | Brier | Slope | CITL |
|---|---|---|---|---|---|
| CatBoost | 0.696 | 0.702 (0.612–0.786) | 0.0069 | 0.69 | +0.056 |
| Logistic (L2) | 0.679 | 0.666 (0.580–0.752) | 0.0069 | 0.78 | +0.010 |
| XGBoost | nan | 0.646 (0.535–0.745) | 0.0070 | 0.41 | -0.003 |
| LightGBM | 0.667 | 0.644 (0.538–0.748) | 0.0073 | 0.21 | +1.539 |
| Random Forest | 0.683 | 0.635 (0.546–0.716) | 0.0069 | 0.68 | -0.005 |

> CatBoost highest but inside the logistic CI (0.580–0.752) — not a real difference. Ship logistic.

**poor_outcome_6m**

| Family | CV AUC | Test AUC (95% CI) | Brier | Slope | CITL |
|---|---|---|---|---|---|
| CatBoost | 0.799 | 0.802 (0.788–0.816) | 0.1725 | 1.04 | +0.045 |
| XGBoost | nan | 0.802 (0.789–0.816) | 0.1726 | 1.01 | +0.042 |
| Logistic (L2) | 0.797 | 0.802 (0.787–0.815) | 0.1730 | 1.04 | +0.045 |
| Random Forest | 0.795 | 0.799 (0.785–0.813) | 0.1744 | 1.20 | +0.038 |
| LightGBM | 0.794 | 0.799 (0.785–0.813) | 0.1740 | 0.98 | +0.048 |

> CatBoost highest but inside the logistic CI (0.787–0.815) — not a real difference. Ship logistic.

**nonadherence_6m**

| Family | CV AUC | Test AUC (95% CI) | Brier | Slope | CITL |
|---|---|---|---|---|---|
| Random Forest | 0.632 | 0.643 (0.616–0.669) | 0.1447 | 1.41 | +0.102 |
| XGBoost | nan | 0.640 (0.614–0.666) | 0.1447 | 0.89 | +0.114 |
| CatBoost | 0.624 | 0.640 (0.613–0.666) | 0.1446 | 0.98 | +0.111 |
| Logistic (L2) | 0.623 | 0.626 (0.600–0.652) | 0.1451 | 0.98 | +0.109 |
| LightGBM | 0.611 | 0.622 (0.596–0.648) | 0.1462 | 0.72 | +0.128 |

> Random Forest highest but inside the logistic CI (0.600–0.652) — not a real difference. Ship logistic.

## 9. Final models

| Outcome | Events | Test AUC (95% CI) | Brier | Slope | CITL | Calibrated |
|---|---|---|---|---|---|---|
| `death_14d` | 1,430 | 0.793 (0.769–0.818) | 0.0724 | 0.94 | -0.025 | not needed |
| `recurrence_14d` | 313 | 0.590 (0.530–0.646) | 0.0206 | 0.69 | +0.051 | not needed |
| `haemorrhage_14d` | 120 | 0.732 (0.660–0.802) | 0.0104 | 0.91 | +0.330 | n/a — split imbalance |
| `pe_14d` | 107 | 0.666 (0.580–0.752) | 0.0069 | 0.78 | +0.010 | not needed |
| `poor_outcome_6m` | 9,679 | 0.802 (0.787–0.815) | 0.1730 | 1.04 | +0.045 | not needed |
| `nonadherence_6m` | 1,901 | 0.626 (0.600–0.652) | 0.1451 | 0.98 | +0.109 | not needed |

## 10. Top drivers

- **death_14d**: level of consciousness, number of deficits present, age, visual field loss, delay from onset to treatment
- **recurrence_14d**: CT performed before treatment, aspirin allocation, heparin allocation = N, delay from onset to treatment, visuospatial difficulty
- **haemorrhage_14d**: delay from onset to treatment, heparin allocation = M, number of deficits present, heparin allocation = N, age
- **pe_14d**: number of deficits present, visual field loss, leg or foot weakness, heparin allocation = M, other neurological deficit
- **poor_outcome_6m**: age, level of consciousness, number of deficits present, CT performed before treatment, deficits that could not be assessed
- **nonadherence_6m**: aspirin in preceding 3 days, aspirin allocation, age, CT performed before treatment, infarct visible on CT

## 11. Decision curve analysis

| Outcome | Useful threshold range | Max net benefit |
|---|---|---|
| `death_14d` | 0.01–0.48 | 0.0813 |
| `recurrence_14d` | 0.02–0.02 | 0.0111 |
| `haemorrhage_14d` | 0.01–0.03 | 0.0036 |
| `pe_14d` | 0.01–0.01 | 0.0002 |
| `poor_outcome_6m` | 0.04–0.50 | 0.6295 |
| `nonadherence_6m` | 0.04–0.42 | 0.1746 |

## 12. Subgroup performance

| Outcome | Subgroup | n | Events | AUC |
|---|---|---|---|---|
| `death_14d` | age <65 | 1,038 | 65 | 0.817 |
| `death_14d` | age 65-74 | 1,248 | 78 | 0.762 |
| `death_14d` | age 75+ | 1,601 | 208 | 0.771 |
| `death_14d` | sex female | 1,830 | 178 | 0.783 |
| `death_14d` | sex male | 2,057 | 173 | 0.799 |
| `recurrence_14d` | age <65 | 1,038 | 22 | 0.618 |
| `recurrence_14d` | age 65-74 | 1,248 | 28 | 0.591 |
| `recurrence_14d` | age 75+ | 1,601 | 32 | 0.571 |
| `recurrence_14d` | sex female | 1,830 | 37 | 0.556 |
| `recurrence_14d` | sex male | 2,057 | 45 | 0.617 |
| `haemorrhage_14d` | age <65 | 1,038 | 12 | 0.584 |
| `haemorrhage_14d` | age 65-74 | 1,248 | 14 | 0.759 |
| `haemorrhage_14d` | age 75+ | 1,601 | 15 | 0.801 |
| `haemorrhage_14d` | sex female | 1,830 | 18 | 0.780 |
| `haemorrhage_14d` | sex male | 2,057 | 23 | 0.689 |
| `pe_14d` | age <65 | 1,038 | 8 | 0.588 |
| `pe_14d` | age 65-74 | 1,248 | 8 | 0.793 |
| `pe_14d` | age 75+ | 1,601 | 11 | 0.622 |
| `pe_14d` | sex female | 1,830 | 14 | 0.663 |
| `pe_14d` | sex male | 2,057 | 13 | 0.658 |
| `poor_outcome_6m` | age <65 | 1,028 | 464 | 0.756 |
| `poor_outcome_6m` | age 65-74 | 1,245 | 738 | 0.771 |
| `poor_outcome_6m` | age 75+ | 1,590 | 1,244 | 0.778 |
| `poor_outcome_6m` | sex female | 1,818 | 1,282 | 0.815 |
| `poor_outcome_6m` | sex male | 2,045 | 1,164 | 0.776 |
| `nonadherence_6m` | age <65 | 873 | 150 | 0.587 |
| `nonadherence_6m` | age 65-74 | 988 | 157 | 0.651 |
| `nonadherence_6m` | age 75+ | 956 | 208 | 0.627 |
| `nonadherence_6m` | sex female | 1,263 | 265 | 0.622 |
| `nonadherence_6m` | sex male | 1,554 | 250 | 0.617 |

## 13. Versus the trial's own estimates

| Comparator | Outcome | Trial AUC | Our AUC |
|---|---|---|---|
| `EXPD14` | `death_14d` | 0.787 | 0.793 |
| `EXPDD` | `poor_outcome_6m` | 0.792 | 0.802 |