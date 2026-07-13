"""
Trains the anomaly_agent's XGBoost classifier on data/transactions.csv +
data/user_profiles.csv (run ml/prepare_paysim.py first), evaluates on a
held-out split, and saves the model + a SHAP explainer to ml/models/.
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import shap
import xgboost as xgb
from sklearn.metrics import confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.feature_engineering import FEATURE_COLUMNS, build_features  # noqa: E402

DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "ml" / "models"


def load_dataset() -> tuple[pd.DataFrame, pd.Series]:
    transactions = pd.read_csv(DATA_DIR / "transactions.csv")

    rows = [build_features(txn._asdict()) for txn in transactions.itertuples(index=False)]

    X = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    y = transactions["is_fraud_ground_truth"].astype(int)
    return X, y


def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading dataset and building features...")
    X, y = load_dataset()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.1,
        scale_pos_weight=scale_pos_weight,
        eval_metric="auc",
        random_state=42,
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    roc_auc = roc_auc_score(y_test, proba)

    threshold = 0.5
    preds = (proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_test, preds).ravel()

    print(f"ROC-AUC: {roc_auc:.4f}")
    print(f"Confusion matrix @ threshold={threshold}: TN={tn} FP={fp} FN={fn} TP={tp}")
    print(f"False positive rate: {fp / max(fp + tn, 1):.4f}")
    print(f"False negative rate: {fn / max(fn + tp, 1):.4f}")

    explainer = shap.TreeExplainer(model)

    joblib.dump(model, MODELS_DIR / "anomaly_model.joblib")
    joblib.dump(explainer, MODELS_DIR / "shap_explainer.joblib")

    metrics = {
        "roc_auc": roc_auc,
        "threshold": threshold,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "false_positive_rate": fp / max(fp + tn, 1),
        "false_negative_rate": fn / max(fn + tp, 1),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "feature_columns": FEATURE_COLUMNS,
    }
    with open(MODELS_DIR / "training_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"Saved model, SHAP explainer, and metrics to {MODELS_DIR}")


if __name__ == "__main__":
    main()
