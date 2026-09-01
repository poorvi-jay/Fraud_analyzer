"""
Diagnostic: how well do policy_agent's illustrative thresholds
(LARGE_REPORTING_THRESHOLD, MULE_DRAIN_MULTIPLE) fit the amount distribution
actually in data/transactions.csv? Run this whenever you swap in a new
dataset (e.g. real PaySim) before trusting evaluate_baseline.py's numbers --
thresholds calibrated for one dataset's scale can badly over/under-trigger
on another.

READS THE TRAINING SPLIT ONLY, BY DEFAULT.
-----------------------------------------
This script exists to inform threshold choices, which makes it a tuning
surface: anything it prints is a number a human might adjust a threshold in
response to. An earlier version computed every statistic over the whole
dataset, including the rows evaluate_baseline.py reports against, and
printed fraud-conditioned outcomes ("N of those are FALSE triggers") for
them. Nothing appears to have actually been tuned that way -- the mule and
new-account constants have been unchanged since commit 144fd69 (Phase 5),
which predates the evaluation harness entirely -- but the capability was
there, and "we checked and nobody used the loaded gun" is a weaker claim
than "there is no loaded gun".

So the default is the training split, reproducing train_anomaly_model.py's
split call exactly (test_size=0.25, random_state=42, stratify on
is_fraud_ground_truth). Pass --include-test to see the held-out numbers
anyway; it prints a warning, and you should not change a threshold on the
strength of what it shows.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.agents.policy_agent import LARGE_REPORTING_THRESHOLD, MULE_DRAIN_MULTIPLE, MULE_DRAIN_RATIO  # noqa: E402

DATA_DIR = ROOT / "data"


def describe_split(txns: pd.DataFrame, profiles: pd.DataFrame, label: str):
    print(f"\n{'=' * 70}")
    print(f"{label}: {len(txns):,} transactions")
    print(f"{'=' * 70}")

    print("\n--- Amount distribution ---")
    print(txns["amount"].describe(percentiles=[0.5, 0.75, 0.9, 0.95, 0.99, 0.999]))

    print("\n--- Amount distribution by transaction_type ---")
    print(txns.groupby("transaction_type")["amount"].describe(percentiles=[0.5, 0.9, 0.99]))

    over_threshold = (txns["amount"] >= LARGE_REPORTING_THRESHOLD).mean()
    print(f"\nCurrent LARGE_REPORTING_THRESHOLD = ${LARGE_REPORTING_THRESHOLD:,.0f}")
    print(f"  -> {over_threshold:.2%} of these transactions exceed it "
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
    print(f"  -> {mule_pattern.mean():.2%} of these transactions trigger it")
    print(f"  -> {(mule_pattern & (txns['is_fraud_ground_truth'] == False)).sum():,} of those are FALSE triggers (legit transactions)")

    print("\n--- Per-user transaction counts (affects how noisy 'typical_transaction_amount' is) ---")
    counts = txns["user_id"].value_counts()
    print(counts.describe(percentiles=[0.1, 0.25, 0.5]))
    print(f"Users with only 1 transaction: {(counts == 1).sum():,} ({(counts == 1).mean():.1%} of users)")

    print("\n--- Percentile reference for recalibration ---")
    print(f"99th percentile amount:   ${txns['amount'].quantile(0.99):,.2f}")
    print(f"99.5th percentile amount: ${txns['amount'].quantile(0.995):,.2f}  "
          f"<- what prepare_paysim.py calibrates LARGE_REPORTING_THRESHOLD to")
    print(f"99.9th percentile amount: ${txns['amount'].quantile(0.999):,.2f}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--include-test", action="store_true",
        help="Also print the held-out test split's numbers. Diagnostic only -- do NOT tune a "
             "threshold in response to what this shows; that is the leak this flag exists to "
             "make visible rather than accidental.",
    )
    args = parser.parse_args()

    txns = pd.read_csv(DATA_DIR / "transactions.csv")
    profiles = pd.read_csv(DATA_DIR / "user_profiles.csv").set_index("user_id")

    # Same split call, same order, same seed as train_anomaly_model.py and
    # prepare_paysim.py's calibrate_on_train_split -> identical partition.
    train_txns, test_txns = train_test_split(
        txns, test_size=0.25, random_state=42, stratify=txns["is_fraud_ground_truth"]
    )

    print(f"Dataset: {len(txns):,} transactions "
          f"-> {len(train_txns):,} train / {len(test_txns):,} held-out test")
    describe_split(train_txns, profiles, "TRAIN SPLIT (safe to tune against)")

    if args.include_test:
        print("\n\n" + "!" * 70)
        print("!! HELD-OUT TEST SPLIT BELOW -- evaluate_baseline.py reports against these")
        print("!! rows. Look if you must, but do not change a threshold because of what")
        print("!! you see here: that is precisely how a reported metric stops meaning")
        print("!! what it claims to mean.")
        print("!" * 70)
        describe_split(test_txns, profiles, "TEST SPLIT (DO NOT TUNE AGAINST)")
    else:
        print("\n\n(Held-out test split not shown. --include-test prints it, with a warning.)")


if __name__ == "__main__":
    main()
