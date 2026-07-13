"""
Single source of truth for derived signals used by the anomaly model and the
context agent. Used by both ml/train_anomaly_model.py (training) and
app/agents/*.py (live inference), so training and serving can never drift
apart. Operates on plain values only -- no ORM/pydantic dependency -- so
it's trivially importable from the standalone ml/ scripts.

Deliberate design split, matching the PRD's problem statement: the anomaly
model only sees transaction-intrinsic, profile-agnostic statistics (amount,
balance movement, type) -- a real "is this statistically unusual" model has
no per-user behavioral history in production. Profile-relative signals
(is this normal for *this* user -- their typical spend, home country, travel
habits) are context-agent-only. That split is what gives the two agents
something genuine to disagree about, rather than both re-deriving the same
signal.
"""
from datetime import datetime

TRANSACTION_TYPES = ["PAYMENT", "CASH_OUT", "CASH_IN", "TRANSFER", "DEBIT"]
TRAVEL_ORDINAL = {"never": 0, "rare": 1, "frequent": 2}

# --- anomaly_agent's model features: transaction-intrinsic only ---
FEATURE_COLUMNS = [
    "amount",
    "origin_balance_before",
    "origin_balance_after",
    "balance_drained_ratio",
    *[f"type_{t}" for t in TRANSACTION_TYPES],
]


def build_features(transaction: dict) -> dict:
    """transaction is a plain dict of raw field values (see
    docs/ARCHITECTURE.md TRANSACTIONS table for the field names)."""
    amount = float(transaction["amount"])
    before = float(transaction["origin_balance_before"])
    after = float(transaction["origin_balance_after"])

    features = {
        "amount": amount,
        "origin_balance_before": before,
        "origin_balance_after": after,
        "balance_drained_ratio": (before - after) / max(before, 1.0),
    }
    for t in TRANSACTION_TYPES:
        features[f"type_{t}"] = 1.0 if transaction["transaction_type"] == t else 0.0

    return {col: features[col] for col in FEATURE_COLUMNS}


# --- context_agent's profile-relative signals ---
def build_context_signals(transaction: dict, profile: dict) -> dict:
    """profile-relative comparison signals: is this normal for *this* user?
    Used both by the mock heuristic context agent and folded into the
    prompt for the real LLM-backed one.
    """
    amount = float(transaction["amount"])
    typical = max(float(profile["typical_transaction_amount"]), 1.0)

    occurred_at = _as_datetime(transaction["occurred_at"])
    account_created = _as_datetime(profile["account_created"])
    account_age_days = max((occurred_at - account_created).days, 0)

    return {
        "amount_to_typical_ratio": amount / typical,
        "is_foreign": transaction["location_country"] != profile["home_country"],
        "account_age_days": account_age_days,
        "travel_frequency": profile["travel_frequency"],
    }


def _as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
