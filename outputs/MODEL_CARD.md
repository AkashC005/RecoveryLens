# RecoveryLens — Model Card

## Intended use

Decision support at hospital discharge after acute ischaemic stroke. Produces risk estimates for six outcomes plus the factors driving each, to help prioritise rehabilitation referral, monitoring and adherence support.

**Not** for autonomous clinical decisions. Every output is advisory and assumes a clinician in the loop.

## Training data

International Stroke Trial (IST-1): 19,435 patients, 467 hospitals, 36 countries, recruited 1991–1996. Public and fully anonymised.

## Model

L2-penalised logistic regression, 24 features, one model per outcome. Selected over Random Forest, XGBoost, LightGBM and CatBoost: no gradient-boosted model beat it outside its confidence interval, and all calibrated worse on the rare outcomes.

## Performance (held-out test set)

| Outcome | Events | AUC (95% CI) | Cal. slope | CITL |
|---|---|---|---|---|
| death_14d | 1,430 | 0.793 (0.769–0.818) | 0.94 | -0.025 |
| recurrence_14d | 313 | 0.590 (0.530–0.646) | 0.69 | +0.051 |
| haemorrhage_14d | 120 | 0.732 (0.660–0.802) | 0.91 | +0.330 |
| pe_14d | 107 | 0.666 (0.580–0.752) | 0.78 | +0.010 |
| poor_outcome_6m | 9,679 | 0.802 (0.787–0.815) | 1.04 | +0.045 |
| nonadherence_6m | 1,901 | 0.626 (0.600–0.652) | 0.98 | +0.109 |

## Limitations

- **Vintage.** IST-1 predates routine thrombolysis (14 of 19,435 patients received it) and modern stroke units. Absolute probabilities require recalibration before contemporary use. Discrimination — the ranking of who is higher risk — transfers more reliably than calibration.
- **No NIHSS.** IST-1 records consciousness on a three-level scale rather than a formal severity score. Published models reaching AUC ~0.85 (ASTRAL, PLAN) use NIHSS; that gap is a data limitation, not a modelling one.
- **`recurrence_14d` is weak** and should be presented as exploratory. Early ischaemic recurrence appears largely unpredictable from presentation alone.
- **Low EPV** on `haemorrhage_14d` and `pe_14d`. Confidence intervals are wide and reported as such.
- **Population.** Predominantly European recruitment; performance in South Asian populations is unvalidated.

## Deployment status

Research prototype. Not validated prospectively, and not approved for clinical use. Real deployment would require local recalibration, a silent validation period with predictions logged but not acted upon, and institutional ethics approval.