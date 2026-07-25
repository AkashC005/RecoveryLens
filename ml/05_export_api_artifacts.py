"""
RecoveryLens — ml/05_export_api_artifacts.py
============================================
Run once, after the notebook. Produces the artifacts the API needs at runtime.

Why tiers instead of raw probabilities
--------------------------------------
The model was trained on 1991-96 data, so absolute probabilities are not
calibrated to modern care. What transfers reliably is the *ranking*. So the API
reports a tier derived from where a patient falls in the training distribution,
and the UI leads with that rather than a number. This is the same reasoning
behind the "no gauges, no big percentages" rule in the design brief.

Outputs
-------
  api/artifacts/thresholds.json    percentile cut-points per outcome
  api/artifacts/schema.json        form options for the frontend
  api/artifacts/metrics.json       copied from the notebook, for the Evidence screen

Run
---
  python ml/05_export_api_artifacts.py
"""

from pathlib import Path
import json
import shutil

import joblib
import numpy as np
import pandas as pd

OUT = Path("outputs")
MODELS = Path("models")
ART = Path("api/artifacts")
ART.mkdir(parents=True, exist_ok=True)

OUTCOMES = ["death_14d", "recurrence_14d", "haemorrhage_14d",
            "pe_14d", "poor_outcome_6m", "nonadherence_6m"]

# Human-facing metadata. `actionability` comes from the decision curve analysis:
# three outcomes showed net benefit over treat-all across a usable threshold
# range; three showed benefit only at very low thresholds, i.e. they justify
# cheap vigilance rather than an active clinical decision. The UI must not
# present these two groups as equivalent.
META = {
    "death_14d": {
        "label": "Death within 14 days", "horizon_days": 14,
        "actionability": "actionable",
        "note": "Supports monitoring intensity and escalation planning.",
    },
    "recurrence_14d": {
        "label": "Recurrent stroke within 14 days", "horizon_days": 14,
        "actionability": "exploratory",
        "note": "Discrimination near chance; no net benefit at actionable "
                "thresholds. Presented as exploratory only.",
    },
    "haemorrhage_14d": {
        "label": "Cerebral bleed within 14 days", "horizon_days": 14,
        "actionability": "vigilance",
        "note": "Ranks well but net benefit is confined to very low thresholds. "
                "Use for low-cost vigilance, not active intervention.",
    },
    "pe_14d": {
        "label": "Pulmonary embolism within 14 days", "horizon_days": 14,
        "actionability": "vigilance",
        "note": "Low prevalence; supports awareness rather than intervention.",
    },
    "poor_outcome_6m": {
        "label": "Death or dependency at 6 months", "horizon_days": 180,
        "actionability": "actionable",
        "note": "Strongest outcome. Supports rehabilitation referral priority.",
    },
    "nonadherence_6m": {
        "label": "Not on secondary prevention at 6 months", "horizon_days": 180,
        "actionability": "actionable",
        "note": "Patient not on antiplatelet or anticoagulant at follow-up. May "
                "reflect non-adherence or appropriate clinical discontinuation — "
                "the data does not distinguish these. Drives check-in frequency.",
    },
}

# Feature schema the frontend renders its form from, so field options live in
# one place rather than being duplicated in the UI.
SCHEMA = {
    "sections": [
        {
            "title": "Demographics",
            "fields": [
                {"name": "age", "label": "Age (years)", "type": "number",
                 "min": 16, "max": 110, "required": True},
                {"name": "sex", "label": "Sex", "type": "select", "required": True,
                 "options": [{"value": "M", "label": "Male"},
                             {"value": "F", "label": "Female"}]},
            ],
        },
        {
            "title": "Presentation",
            "fields": [
                {"name": "hours_since_onset", "label": "Hours since symptom onset",
                 "type": "number", "min": 0, "max": 48, "required": True},
                {"name": "consciousness", "label": "Level of consciousness",
                 "type": "select", "required": True,
                 "options": [{"value": "alert", "label": "Fully alert"},
                             {"value": "drowsy", "label": "Drowsy"},
                             {"value": "unconscious", "label": "Unconscious"}]},
                {"name": "systolic_bp", "label": "Systolic blood pressure (mmHg)",
                 "type": "number", "min": 60, "max": 300, "required": True},
                {"name": "symptoms_on_waking", "label": "Symptoms noticed on waking",
                 "type": "boolean", "required": False},
                {"name": "stroke_subtype", "label": "Stroke subtype (Bamford)",
                 "type": "select", "required": True,
                 "options": [{"value": "TACS", "label": "TACS — total anterior circulation"},
                             {"value": "PACS", "label": "PACS — partial anterior circulation"},
                             {"value": "LACS", "label": "LACS — lacunar"},
                             {"value": "POCS", "label": "POCS — posterior circulation"},
                             {"value": "OTH", "label": "Other / unclassified"}]},
            ],
        },
        {
            "title": "Neurological deficits",
            "help": "‘Cannot assess’ is recorded as its own value — it carries "
                    "real prognostic weight and is not treated as missing.",
            "fields": [
                {"name": f"deficit_{k}", "label": v, "type": "deficit",
                 "required": True,
                 "options": [{"value": "absent", "label": "Absent"},
                             {"value": "present", "label": "Present"},
                             {"value": "cannot_assess", "label": "Cannot assess"}]}
                for k, v in [
                    ("face", "Facial weakness"),
                    ("arm", "Arm or hand weakness"),
                    ("leg", "Leg or foot weakness"),
                    ("speech", "Difficulty speaking (dysphasia)"),
                    ("visual_field", "Visual field loss"),
                    ("visuospatial", "Visuospatial difficulty"),
                    ("brainstem", "Brainstem or cerebellar signs"),
                    ("other", "Other neurological deficit"),
                ]
            ],
        },
        {
            "title": "History and imaging",
            "fields": [
                {"name": "atrial_fibrillation", "label": "Atrial fibrillation",
                 "type": "select", "required": True,
                 "options": [{"value": "yes", "label": "Yes"},
                             {"value": "no", "label": "No"},
                             {"value": "unknown", "label": "Unknown"}]},
                {"name": "ct_before_treatment", "label": "CT performed before treatment",
                 "type": "boolean", "required": False},
                {"name": "infarct_visible_on_ct", "label": "Infarct visible on CT",
                 "type": "boolean", "required": False},
                {"name": "heparin_last_24h", "label": "Heparin in preceding 24 hours",
                 "type": "boolean", "required": False},
                {"name": "aspirin_last_3days", "label": "Aspirin in preceding 3 days",
                 "type": "boolean", "required": False},
            ],
        },
        {
            "title": "Planned antithrombotic treatment",
            "help": "Maps to the trial's randomised allocation variables.",
            "fields": [
                {"name": "planned_aspirin", "label": "Aspirin planned",
                 "type": "boolean", "required": False},
                {"name": "planned_heparin", "label": "Heparin planned",
                 "type": "select", "required": True,
                 "options": [{"value": "none", "label": "None"},
                             {"value": "low", "label": "Low dose"},
                             {"value": "medium", "label": "Medium dose"}]},
            ],
        },
    ]
}


def main() -> None:
    print("Loading clean data and split...")
    data = pd.read_csv(OUT / "ist1_clean.csv")
    split = pd.read_csv(OUT / "split_indices.csv").set_index("row")["split"]
    train_idx = split[split == "train"].index

    features = [c for c in data.columns if c not in OUTCOMES]

    thresholds = {}
    for outcome in OUTCOMES:
        model = joblib.load(MODELS / f"final_{outcome}.pkl")
        sub = data[data[outcome].notna()]
        rows = sub.index.intersection(train_idx)
        probs = model.predict_proba(sub.loc[rows, features])[:, 1]

        thresholds[outcome] = {
            **META[outcome],
            "p50": float(np.percentile(probs, 50)),
            "p80": float(np.percentile(probs, 80)),
            "p95": float(np.percentile(probs, 95)),
            "train_prevalence": float(sub.loc[rows, outcome].mean()),
        }
        print(f"  {outcome:20s} p50={thresholds[outcome]['p50']:.4f} "
              f"p80={thresholds[outcome]['p80']:.4f} "
              f"p95={thresholds[outcome]['p95']:.4f}")

    (ART / "thresholds.json").write_text(json.dumps(thresholds, indent=2))
    (ART / "schema.json").write_text(json.dumps(SCHEMA, indent=2))

    src = OUT / "final_metrics.json"
    if src.exists():
        shutil.copy(src, ART / "metrics.json")
        print("  copied metrics.json")
    else:
        print("  WARNING: outputs/final_metrics.json not found — "
              "the Evidence screen will have no data")

    print(f"\nWrote artifacts to {ART}/")


if __name__ == "__main__":
    main()
