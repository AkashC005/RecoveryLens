# Model Benchmark — five families, five outcomes

Mode: **FULL** · bootstrap resamples: 1000 · seed: 42

Reusing the Day 2 split: 15,548 train / 3,887 test.

Families benchmarked: Logistic (L2), Random Forest, XGBoost, LightGBM, CatBoost

Total runtime: 4.2 min

## death_14d

| Family | CV AUC | Test AUC (95% CI) | Brier | Cal. slope | CITL |
|---|---|---|---|---|---|
| XGBoost | 0.808 | 0.793 (0.768–0.817) | 0.0730 | 0.85 | -0.035 |
| Logistic (L2) | 0.809 | 0.792 (0.767–0.818) | 0.0726 | 0.94 | -0.027 |
| CatBoost | 0.811 | 0.792 (0.767–0.818) | 0.0723 | 0.90 | -0.032 |
| Random Forest | 0.809 | 0.791 (0.766–0.817) | 0.0724 | 1.05 | -0.028 |
| LightGBM | 0.801 | 0.785 (0.760–0.811) | 0.0740 | 0.79 | -0.022 |

> XGBoost scores highest but sits **inside** the logistic CI (0.767–0.818) — not a real difference. **Ship logistic** and keep clean SHAP.

## recurrence_14d

| Family | CV AUC | Test AUC (95% CI) | Brier | Cal. slope | CITL |
|---|---|---|---|---|---|
| Random Forest | 0.570 | 0.590 (0.528–0.651) | 0.0206 | 1.72 | +0.054 |
| Logistic (L2) | 0.580 | 0.584 (0.529–0.643) | 0.0206 | 0.73 | +0.046 |
| XGBoost | 0.575 | 0.577 (0.517–0.636) | 0.0207 | 0.40 | +0.078 |
| CatBoost | 0.573 | 0.530 (0.461–0.588) | 0.0208 | 0.10 | +0.129 |
| LightGBM | 0.569 | 0.517 (0.457–0.578) | 0.0214 | 0.06 | +0.383 |

> Random Forest scores highest but sits **inside** the logistic CI (0.529–0.643) — not a real difference. **Ship logistic** and keep clean SHAP.

## haemorrhage_14d

| Family | CV AUC | Test AUC (95% CI) | Brier | Cal. slope | CITL |
|---|---|---|---|---|---|
| Logistic (L2) | 0.679 | 0.667 (0.584–0.746) | 0.0104 | 0.93 | +0.309 |
| Random Forest | 0.674 | 0.657 (0.564–0.737) | 0.0104 | 1.07 | +0.321 |
| CatBoost | 0.637 | 0.621 (0.543–0.701) | 0.0105 | 0.52 | +0.362 |
| XGBoost | 0.653 | 0.606 (0.515–0.693) | 0.0106 | 0.32 | +0.318 |
| LightGBM | 0.607 | 0.506 (0.418–0.596) | 0.0110 | 0.01 | +1.023 |

> Logistic wins outright — **ship the interpretable model**.

## pe_14d

| Family | CV AUC | Test AUC (95% CI) | Brier | Cal. slope | CITL |
|---|---|---|---|---|---|
| Logistic (L2) | 0.649 | 0.652 (0.560–0.740) | 0.0069 | 0.80 | -0.007 |
| Random Forest | 0.680 | 0.636 (0.547–0.717) | 0.0069 | 0.80 | +0.003 |
| XGBoost | 0.705 | 0.616 (0.528–0.709) | 0.0071 | 0.25 | -0.016 |
| CatBoost | 0.670 | 0.609 (0.510–0.707) | 0.0070 | 0.33 | +0.043 |
| LightGBM | 0.659 | 0.592 (0.482–0.702) | 0.0072 | 0.18 | +0.207 |

> Logistic wins outright — **ship the interpretable model**.

## poor_outcome_6m

| Family | CV AUC | Test AUC (95% CI) | Brier | Cal. slope | CITL |
|---|---|---|---|---|---|
| CatBoost | 0.794 | 0.797 (0.783–0.811) | 0.1748 | 1.03 | +0.052 |
| Random Forest | 0.790 | 0.796 (0.782–0.810) | 0.1769 | 1.27 | +0.037 |
| XGBoost | 0.793 | 0.795 (0.781–0.809) | 0.1756 | 0.99 | +0.052 |
| Logistic (L2) | 0.790 | 0.795 (0.781–0.810) | 0.1759 | 1.03 | +0.052 |
| LightGBM | 0.789 | 0.792 (0.778–0.806) | 0.1771 | 0.96 | +0.060 |

> CatBoost scores highest but sits **inside** the logistic CI (0.781–0.810) — not a real difference. **Ship logistic** and keep clean SHAP.

## Selection summary

| Outcome | Best family | Test AUC | Beats baseline outside CI? |
|---|---|---|---|
| `death_14d` | XGBoost | 0.793 | no |
| `recurrence_14d` | Random Forest | 0.590 | no |
| `haemorrhage_14d` | Logistic (L2) | 0.667 | — |
| `pe_14d` | Logistic (L2) | 0.652 | — |
| `poor_outcome_6m` | CatBoost | 0.797 | no |

Next: calibrate the selected models (isotonic where events allow, Platt where sparse), then fit SHAP explainers. Calibration matters more than the third decimal place of AUC — it is what recalibration will have to fix when this model meets modern-era data.