"""Anomaly agent: the trained XGBoost model, with SHAP-based reasoning.

Scores purely on transaction-intrinsic statistics (see feature_engineering.py
for why profile-relative signals are deliberately excluded here).
"""
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd

from app.agents.base import AgentOpinion
from app.config import settings
from app.feature_engineering import FEATURE_COLUMNS, build_features

MODELS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "ml" / "models"

_FEATURE_LABELS = {
    "amount": "transaction amount",
    "origin_balance_before": "balance before the transaction",
    "origin_balance_after": "balance after the transaction",
    "balance_drained_ratio": "share of balance drained",
}
for _t in ["PAYMENT", "CASH_OUT", "CASH_IN", "TRANSFER", "DEBIT"]:
    _FEATURE_LABELS[f"type_{_t}"] = f"transaction type = {_t}"


@lru_cache(maxsize=1)
def _load_model():
    model = joblib.load(MODELS_DIR / "anomaly_model.joblib")
    explainer = joblib.load(MODELS_DIR / "shap_explainer.joblib")
    return model, explainer


def _describe_shap(features: dict, shap_values, top_n=3) -> str:
    contributions = sorted(
        zip(FEATURE_COLUMNS, shap_values), key=lambda pair: abs(pair[1]), reverse=True
    )[:top_n]
    parts = []
    for name, value in contributions:
        if abs(value) < 1e-4:
            continue
        direction = "raised" if value > 0 else "lowered"
        parts.append(f"{_FEATURE_LABELS.get(name, name)} ({features[name]:.2f}) {direction} the score ({value:+.3f})")
    return "; ".join(parts) if parts else "no single feature dominated the score"


def run(transaction: dict) -> AgentOpinion:
    model, explainer = _load_model()
    features = build_features(transaction)
    X = pd.DataFrame([features], columns=FEATURE_COLUMNS)

    score = float(model.predict_proba(X)[0, 1])
    shap_values = explainer.shap_values(X)[0]
    flag = score >= settings.anomaly_high_threshold

    level = "high" if flag else "low"
    reasoning = (
        f"Anomaly score {score:.2f} ({level}). Top contributing factors: {_describe_shap(features, shap_values)}."
    )
    return AgentOpinion(agent_name="anomaly_agent", score=score, flag=flag, reasoning=reasoning)
