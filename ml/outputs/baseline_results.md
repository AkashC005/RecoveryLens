# Baseline Results — Penalised Logistic Regression

## 1. Train/test split

- Loaded 19,435 rows
- Train: **15,548** · Test: **3,887** (80/20, seed 42)
- Stratified on `pe_14d` (rarest outcome)

Event counts per split — check the rare outcomes have enough in both halves:

| Outcome | Train events | Test events |
|---|---|---|
| `death_14d` | 1,430 | 351 |
| `recurrence_14d` | 313 | 82 |
| `haemorrhage_14d` | 120 | 41 |
| `pe_14d` | 107 | 27 |
| `poor_outcome_6m` | 9,679 | 2,446 |

## 2. Baseline performance (held-out test set)

Regularisation strength `C` tuned by 5-fold CV **inside the training set only**. Models are fitted unweighted: class weighting shifts the intercept and degrades calibration, which matters more here than a marginal AUC gain.

| Outcome | Features | Params | Test AUC (95% CI) | Brier | Cal. slope | CITL |
|---|---|---|---|---|---|---|
| `death_14d` | full | 22 | 0.792 (0.767–0.818) | 0.0726 | 0.94 | -0.027 |
| `recurrence_14d` | full | 22 | 0.584 (0.529–0.643) | 0.0206 | 0.73 | +0.046 |
| `haemorrhage_14d` | core | 13 | 0.667 (0.584–0.746) | 0.0104 | 0.93 | +0.309 |
| `pe_14d` | core | 13 | 0.652 (0.560–0.740) | 0.0069 | 0.80 | -0.007 |
| `poor_outcome_6m` | full | 22 | 0.795 (0.781–0.810) | 0.1759 | 1.03 | +0.052 |

**Reading the calibration columns.** Slope 1.0 and CITL 0.0 are ideal. Slope below 1 means predictions are too extreme; CITL below 0 means the model over-predicts risk overall. Both are correctable by recalibration — and both are the metrics that will drift when this model meets modern-era data, which is exactly the argument already made about IST-1's vintage.

## 3. Selected regularisation

| Outcome | C |
|---|---|
| `death_14d` | 0.1 |
| `recurrence_14d` | 10.0 |
| `haemorrhage_14d` | 0.1 |
| `pe_14d` | 10.0 |
| `poor_outcome_6m` | 0.1 |

Models saved to `ml/models/`. Split saved to `ml/outputs/split_indices.csv` — **every later script must load this split, not create its own.**