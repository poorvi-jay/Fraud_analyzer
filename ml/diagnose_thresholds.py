"""
One-off diagnostic: how well do policy_agent's illustrative thresholds
(LARGE_REPORTING_THRESHOLD, MULE_DRAIN_MULTIPLE) fit the amount distribution
actually in data/transactions.csv? Run this whenever you swap in a new
dataset (e.g. real PaySim) before trusting evaluate_baseline.py's numbers --
thresholds calibrated for one dataset's scale can badly over/under-trigger
on another.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.agents.policy_agent import LARGE_REPORTING_THRESHOLD, MULE_DRAIN_MULTIPLE, MULE_DRAIN_RATIO  # noqa: E402

DATA_DIR = ROOT / "data"


def main():
    txns = pd.read_csv(DATA_DIR / "transactions.csv")
    profiles = pd.read_csv(DATA_DIR / "user_profiles.csv").set_index("user_id")

    print(f"Total transactions: {len(txns):,}")
    print(f"\n--- Amount distribution (all transactions) ---")
    print(txns["amount"].describe(percentiles=[0.5, 0.75, 0.9, 0.95, 0.99, 0.999]))

    print(f"\n--- Amount distribution by transaction_type ---")
    print(txns.groupby("transaction_type")["amount"].describe(percentiles=[0.5, 0.9, 0.99]))

    over_threshold = (txns["amount"] >= LARGE_REPORTING_THRESHOLD).mean()
    print(f"\nCurrent LARGE_REPORTING_THRESHOLD = ${LARGE_REPORTING_THRESHOLD:,.0f}")
    print(f"  -> {over_threshold:.2%} of ALL transactions exceed it "
          f"(this alone forces a policy flag -> auto-block on the coordinator table)")

    drained_ratio = (txns["origin_balance_before"] - txns["origin_balance_after"]) / txns["origin_balance_before"].clip(lower=1)
    typical = txns["user_id"].map(profiles["typical_transaction_amount"])
    mule_multiple = txns["amount"] / typical.clip(lower=1)
    mule_pattern = (
        txns["transaction_type"].isin(["TRANSFER", "CASH_OUT"])
        & (mule_multiple >= MULE_DRAIN_MULTIPLE)
        & (drained_ratio >= MULE_DRAIN_RATIO)
    )
    print(f"\nCurrent mule-pattern rule (amount >= {MULE_DRAIN_MULTIPLE}x typical AND drained >= {MULE_DRAIN_RATIO:.0%}):")
    print(f"  -> {mule_pattern.mean():.2%} of ALL transactions trigger it")
    print(f"  -> {(mule_pattern & (txns['is_fraud_ground_truth'] == False)).sum():,} of those are FALSE triggers (legit transactions)")

    print(f"\n--- Per-user transaction counts (affects how noisy 'typical_transaction_amount' is) ---")
    counts = txns["user_id"].value_counts()
    print(counts.describe(percentiles=[0.1, 0.25, 0.5]))
    print(f"Users with only 1 transaction: {(counts == 1).sum():,} ({(counts == 1).mean():.1%} of users)")

    print(f"\n--- Suggested recalibrated threshold ---")
    p99 = txns["amount"].quantile(0.99)
    p999 = txns["amount"].quantile(0.999)
    print(f"99th percentile amount: ${p99:,.2f}")
    print(f"99.9th percentile amount: ${p999:,.2f}")


if __name__ == "__main__":
    main()
