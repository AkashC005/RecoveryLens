"""
RecoveryLens API — predictor.py
===============================
Loads the six models once at startup, translates a clinical request into the
model's feature vector, predicts, explains, and assigns a tier.

Design decision carried from the ML work
----------------------------------------
The API returns a probability, but the *tier* is the headline. Training data is
from 1991-96, so absolute probabilities are not calibrated to modern care while
the ranking transfers reliably. Tiers come from percentiles of the training
distribution — a patient is "elevated" because they sit above the 80th
percentile of comparable patients, not because a number crossed an arbitrary line.
"""

from pathlib import Path
import json

import joblib
import numpy as np
import pandas as pd

MODELS = Path(__file__).resolve().parent.parent / "models"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"

OUTCOMES = ["death_14d", "recurrence_14d", "haemorrhage_14d",
            "pe_14d", "poor_outcome_6m", "nonadherence_6m"]

# Must match the notebook's feature order exactly.
NUMERIC = ["AGE", "RDELAY", "RSBP", "DEFCOUNT", "DEFUNASSESSED"]
DEFICITS = [f"RDEF{i}" for i in range(1, 9)]
EXTRA = ["RSLEEP", "RCT", "RVISINF", "RHEP24", "RASP3"]
BINARY_ORDINAL = ["SEX", "RXASP", "RCONSC_ord"] + DEFICITS + EXTRA
CATEGORICAL = ["STYPE", "RATRIAL_cat", "RXHEP"]
FEATURES = NUMERIC + BINARY_ORDINAL + CATEGORICAL

_DEFICIT_MAP = {"absent": 0, "present": 1, "cannot_assess": 2}
_CONSC_MAP = {"alert": 0, "drowsy": 1, "unconscious": 2}
_HEPARIN_MAP = {"none": "N", "low": "L", "medium": "M"}

# request field -> model column, in the notebook's RDEF1..RDEF8 order
_DEFICIT_FIELDS = [
    ("deficit_face", "RDEF1"), ("deficit_arm", "RDEF2"),
    ("deficit_leg", "RDEF3"), ("deficit_speech", "RDEF4"),
    ("deficit_visual_field", "RDEF5"), ("deficit_visuospatial", "RDEF6"),
    ("deficit_brainstem", "RDEF7"), ("deficit_other", "RDEF8"),
]

DISCLAIMER = (
    "Research prototype. Trained on trial data from 1991-96, which predates "
    "routine thrombolysis. Risk tiers indicate relative priority, not calibrated "
    "probability. Advisory only — not a substitute for clinical judgement."
)


class Predictor:
    def __init__(self) -> None:
        self.models: dict = {}
        self.explainers: dict = {}
        self.thresholds: dict = {}
        self.schema: dict = {}
        self.metrics: dict = {}
        self.loaded = False

    def load(self) -> None:
        """Called once on startup. Fails loudly — a silently model-less API is worse."""
        missing = [o for o in OUTCOMES if not (MODELS / f"final_{o}.pkl").exists()]
        if missing:
            raise FileNotFoundError(
                f"Missing models: {missing}. Run the notebook, then "
                f"ml/05_export_api_artifacts.py.")

        for outcome in OUTCOMES:
            self.models[outcome] = joblib.load(MODELS / f"final_{outcome}.pkl")
            path = MODELS / f"explainer_{outcome}.pkl"
            self.explainers[outcome] = joblib.load(path) if path.exists() else None

        self.thresholds = json.loads((ARTIFACTS / "thresholds.json").read_text())
        self.schema = json.loads((ARTIFACTS / "schema.json").read_text())
        metrics_path = ARTIFACTS / "metrics.json"
        self.metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        self.loaded = True

    # ------------------------------------------------------------------ features
    @staticmethod
    def to_features(req) -> pd.DataFrame:
        """Clinical request -> one-row feature frame matching the trained schema."""
        d = req.model_dump() if hasattr(req, "model_dump") else dict(req)

        row: dict = {
            "AGE": float(d["age"]),
            "RDELAY": float(d["hours_since_onset"]),
            "RSBP": float(d["systolic_bp"]),
            "SEX": 1 if str(d["sex"]).upper().endswith("M") else 0,
            "RCONSC_ord": _CONSC_MAP[str(d["consciousness"]).split(".")[-1]],
            "RXASP": int(bool(d["planned_aspirin"])),
            "RSLEEP": int(bool(d["symptoms_on_waking"])),
            "RCT": int(bool(d["ct_before_treatment"])),
            "RVISINF": int(bool(d["infarct_visible_on_ct"])),
            "RHEP24": int(bool(d["heparin_last_24h"])),
            "RASP3": int(bool(d["aspirin_last_3days"])),
            "STYPE": str(d["stroke_subtype"]).split(".")[-1],
            "RATRIAL_cat": {"yes": "Y", "no": "N", "unknown": "unknown"}[
                str(d["atrial_fibrillation"]).split(".")[-1]],
            "RXHEP": _HEPARIN_MAP[str(d["planned_heparin"]).split(".")[-1]],
        }

        for field, col in _DEFICIT_FIELDS:
            row[col] = _DEFICIT_MAP[str(d[field]).split(".")[-1]]

        # Derived, exactly as in the notebook.
        deficit_vals = [row[c] for c in DEFICITS]
        row["DEFCOUNT"] = sum(1 for v in deficit_vals if v >= 1)
        row["DEFUNASSESSED"] = sum(1 for v in deficit_vals if v == 2)

        return pd.DataFrame([row])[FEATURES]

    # ------------------------------------------------------------------ tiering
    def _tier(self, outcome: str, prob: float) -> tuple[str, int]:
        t = self.thresholds[outcome]
        if prob < t["p50"]:
            tier, lo, hi, plo, phi = "low", 0.0, t["p50"], 0, 50
        elif prob < t["p80"]:
            tier, lo, hi, plo, phi = "moderate", t["p50"], t["p80"], 50, 80
        elif prob < t["p95"]:
            tier, lo, hi, plo, phi = "elevated", t["p80"], t["p95"], 80, 95
        else:
            return "high", 97

        span = hi - lo
        frac = (prob - lo) / span if span > 0 else 0.0
        return tier, int(plo + frac * (phi - plo))

    # ------------------------------------------------------------------ explanation
    def _drivers(self, outcome: str, X: pd.DataFrame, top_n: int = 4) -> list[dict]:
        bundle = self.explainers.get(outcome)
        if bundle is None:
            return []
        try:
            Z = bundle["pre"].transform(X)
            vals = np.asarray(bundle["explainer"].shap_values(Z)).ravel()
            names = bundle["names"]
            order = np.argsort(-np.abs(vals))[:top_n]
            return [
                {
                    "factor": names[i],
                    "direction": "increases" if vals[i] > 0 else "decreases",
                    "magnitude": round(float(abs(vals[i])), 4),
                }
                for i in order if abs(vals[i]) > 1e-6
            ]
        except Exception:
            # An explanation failure must never take down a prediction.
            return []

    # ------------------------------------------------------------------ predict
    def predict(self, req) -> dict:
        if not self.loaded:
            raise RuntimeError("Predictor.load() was not called.")

        X = self.to_features(req)
        risks = []

        for outcome in OUTCOMES:
            prob = float(self.models[outcome].predict_proba(X)[0, 1])
            tier, pct = self._tier(outcome, prob)
            meta = self.thresholds[outcome]
            risks.append({
                "outcome": outcome,
                "label": meta["label"],
                "horizon_days": meta["horizon_days"],
                "probability": round(prob, 4),
                "percentile": pct,
                "tier": tier,
                "actionability": meta["actionability"],
                "note": meta["note"],
                "drivers": self._drivers(outcome, X),
            })

        # Order the display by clinical urgency, not alphabetically: actionable
        # outcomes first, highest tier within that.
        rank = {"high": 0, "elevated": 1, "moderate": 2, "low": 3}
        act = {"actionable": 0, "vigilance": 1, "exploratory": 2}
        risks.sort(key=lambda r: (act[r["actionability"]], rank[r["tier"]]))

        return {
            "risks": risks,
            "guidance_triggers": self._guidance(req, risks),
            "followup_plan": self._followup(risks),
            "disclaimer": DISCLAIMER,
        }

    # ------------------------------------------------------------------ downstream
    @staticmethod
    def _guidance(req, risks: list[dict]) -> list[str]:
        """Content categories for the guidance layer (Sprint 4) to retrieve."""
        by = {r["outcome"]: r for r in risks}
        elevated = {"elevated", "high"}
        triggers: list[str] = []

        if by["poor_outcome_6m"]["tier"] in elevated:
            triggers.append("rehabilitation_referral")
        if by["nonadherence_6m"]["tier"] in elevated:
            triggers.append("adherence_support")
        if by["haemorrhage_14d"]["tier"] in elevated:
            triggers.append("bleeding_warning_signs")
        if by["death_14d"]["tier"] == "high":
            triggers.append("close_monitoring")

        # Deficit-specific rehabilitation content.
        d = req.model_dump() if hasattr(req, "model_dump") else dict(req)
        present = "present"
        if str(d["deficit_speech"]).split(".")[-1] == present:
            triggers.append("speech_and_swallowing")
        if str(d["deficit_arm"]).split(".")[-1] == present:
            triggers.append("arm_rehabilitation")
        if str(d["deficit_leg"]).split(".")[-1] == present:
            triggers.append("mobility_and_falls")
        if str(d["deficit_visual_field"]).split(".")[-1] == present:
            triggers.append("visual_field_safety")

        triggers.append("general_recovery")
        return list(dict.fromkeys(triggers))


    @   staticmethod
    def _followup(risks: list[dict]) -> list[dict]:
        """Check-in schedule.

        The days are DETERMINISTIC and are never chosen by a model. Two identical
        patients must receive an identical schedule.

        Provenance for each interval lives in guidance/followup.json, which is
        the single source of truth for whether a day is guideline-backed or ours.
        Do not restate a justification here — it will drift from the evidence file.

        A previous version of this docstring claimed day 14 was a
        "guideline-recommended post-discharge review" and day 30 followed
        "nurse-led follow-up trials". Neither claim could be sourced from any
        guideline in the corpus (NICE NG236, NG128, CG76, ISA 2024, NCGS 2023),
        and the day-14 reason string asserted guideline authority in the API
        response itself. Both are now labelled 'operational' in followup.json.
        Day 42 was added because 4-6 weeks IS cited, twice, and was missing.
        """
        by = {r["outcome"]: r for r in risks}
        plan = [
            {"day": 7,  "reason": "One-week review"},
            {"day": 14, "reason": "Two-week check-in"},
            {"day": 30, "reason": "One-month contact"},
            {"day": 42, "reason": "Six-week review"},
            {"day": 90, "reason": "Three-month outcome point"},
        ]
        # The adaptation is ours, not guideline: patients predicted at elevated
        # risk of stopping secondary prevention get an extra early contact and an
        # extension to the 6-month horizon the model predicts to.
        if by["nonadherence_6m"]["tier"] in {"elevated", "high"}:
            plan.insert(0, {"day": 3, "reason": "Early contact"})
            plan.append({"day": 180, "reason": "Six-month structured review"})
        return sorted(plan, key=lambda p: p["day"])


predictor = Predictor()
