"""
Builds data/transactions.csv and data/user_profiles.csv, the two inputs the
rest of the ML/agent pipeline trains and scores against.

If a real PaySim CSV (Kaggle "Synthetic Financial Datasets For Fraud
Detection") is present at data/paysim.csv, it is adapted into this schema.
Otherwise a synthetic stand-in is generated that mimics PaySim's columns and
its actual fraud pattern (a TRANSFER that drains the origin account, PaySim's
own fraud-injection logic) plus synthetic per-user profiles, since PaySim
itself has no user history. This is a documented limitation (see README) --
swapping in the real CSV later requires no code changes downstream.

Output columns intentionally match the architecture doc's TRANSACTIONS /
USER_PROFILES tables exactly, because the anomaly model is trained on the
same fields the API receives at review time (no destination-account data is
available at inference, so none is used as a model feature).
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REAL_PAYSIM_PATH = DATA_DIR / "paysim.csv"

TRANSACTION_TYPES = ["PAYMENT", "CASH_OUT", "CASH_IN", "TRANSFER", "DEBIT"]
TYPE_WEIGHTS = [0.34, 0.35, 0.22, 0.08, 0.01]
COUNTRIES = ["US", "GB", "DE", "FR", "IN", "BR", "NG", "SG", "AU", "CA"]
TRAVEL_FREQUENCIES = ["never", "rare", "frequent"]
TRAVEL_WEIGHTS = [0.5, 0.35, 0.15]
FOREIGN_TXN_PROB = {"never": 0.01, "rare": 0.08, "frequent": 0.35}


def generate_user_profiles(n_users: int, rng: np.random.Generator) -> pd.DataFrame:
    user_ids = [f"C{100000000 + i}" for i in range(n_users)]
    account_created = pd.to_datetime("2024-01-01") - pd.to_timedelta(
        rng.integers(30, 365 * 4, size=n_users), unit="D"
    )
    home_country = rng.choice(COUNTRIES, size=n_users)
    # lognormal typical amounts, roughly $20 - $5000 with a long tail
    typical_amount = np.round(rng.lognormal(mean=5.5, sigma=1.0, size=n_users), 2)
    travel_frequency = rng.choice(TRAVEL_FREQUENCIES, size=n_users, p=TRAVEL_WEIGHTS)

    return pd.DataFrame(
        {
            "user_id": user_ids,
            "account_created": account_created,
            "home_country": home_country,
            "typical_transaction_amount": typical_amount,
            "travel_frequency": travel_frequency,
        }
    )


def _pick_location(home_country: str, travel_frequency: str, rng: np.random.Generator) -> str:
    if rng.random() < FOREIGN_TXN_PROB[travel_frequency]:
        others = [c for c in COUNTRIES if c != home_country]
        return rng.choice(others)
    return home_country


def simulate_transactions(profiles: pd.DataFrame, rng: np.random.Generator, txns_per_user=(4, 16)) -> pd.DataFrame:
    sim_start = pd.to_datetime("2024-06-01")
    rows = []
    txn_id = 0

    for profile in profiles.itertuples(index=False):
        n_txns = rng.integers(txns_per_user[0], txns_per_user[1])
        balance = max(profile.typical_transaction_amount * rng.uniform(3, 15), 100.0)
        # Frequent travellers occasionally make a large, legitimate,
        # off-profile purchase abroad -- the "explainable anomaly" case the
        # PRD calls out. Bake a few of these in per traveller.
        big_legit_txn_idx = (
            rng.integers(0, n_txns) if profile.travel_frequency == "frequent" and rng.random() < 0.4 else -1
        )

        occurred_at = sim_start + pd.to_timedelta(rng.integers(0, 30), unit="D")
        for i in range(n_txns):
            occurred_at = occurred_at + pd.to_timedelta(rng.integers(1, 48), unit="h")
            txn_type = rng.choice(TRANSACTION_TYPES, p=TYPE_WEIGHTS)

            if i == big_legit_txn_idx:
                # The PRD's canonical "anomalous but explainable" case: a
                # large purchase abroad from a frequent traveller. Anomaly
                # score will be high; context agent should call it plausible.
                amount = round(profile.typical_transaction_amount * rng.uniform(6, 12), 2)
                others = [c for c in COUNTRIES if c != profile.home_country]
                location = rng.choice(others)
                txn_type = rng.choice(["PAYMENT", "CASH_OUT"])
            else:
                amount = round(max(rng.lognormal(mean=np.log(max(profile.typical_transaction_amount, 1)), sigma=0.6), 1.0), 2)
                location = _pick_location(profile.home_country, profile.travel_frequency, rng)

            balance_before = round(balance, 2)
            if txn_type == "CASH_IN":
                amount = min(amount, 20000)
                balance_after = round(balance_before + amount, 2)
            else:
                amount = min(amount, balance_before) if balance_before > 0 else amount
                balance_after = round(max(balance_before - amount, 0.0), 2)
            balance = balance_after

            rows.append(
                {
                    "id": f"T{txn_id:07d}",
                    "user_id": profile.user_id,
                    "amount": amount,
                    "transaction_type": txn_type,
                    "origin_balance_before": balance_before,
                    "origin_balance_after": balance_after,
                    "location_country": location,
                    "is_fraud_ground_truth": False,
                    "occurred_at": occurred_at,
                }
            )
            txn_id += 1

    df = pd.DataFrame(rows)

    # Fraud injection. Two subtypes, deliberately, so the anomaly-only
    # baseline is *not* trivially perfect and the multi-agent pipeline has
    # something real to add (this is the PRD's core thesis):
    #  - "obvious": PaySim's real fraud pattern -- a TRANSFER that drains
    #    the full balance for a wildly-above-typical amount. Easy for the
    #    anomaly model alone.
    #  - "stealthy": amount only modestly above typical and balance barely
    #    touched, so it looks statistically unremarkable -- but it's from a
    #    country the user has no travel history to. The anomaly model alone
    #    misses these (low score); the context agent's profile comparison
    #    is what catches them.
    fraud_rng = rng
    n_fraud = min(len(profiles), max(1, int(len(df) * 0.014)))
    fraud_users = profiles.sample(n=n_fraud, random_state=int(fraud_rng.integers(0, 1_000_000)))
    fraud_rows = []
    for i, profile in enumerate(fraud_users.itertuples(index=False)):
        occurred_at = sim_start + pd.to_timedelta(fraud_rng.integers(0, 30), unit="D") + pd.to_timedelta(
            fraud_rng.integers(0, 48), unit="h"
        )
        others = [c for c in COUNTRIES if c != profile.home_country]
        location = fraud_rng.choice(others)

        if i % 5 < 2:  # ~40% obvious
            amount = round(max(profile.typical_transaction_amount * fraud_rng.uniform(18, 40), 500), 2)
            balance_before, balance_after = amount, 0.0
        else:  # ~60% stealthy
            amount = round(max(profile.typical_transaction_amount * fraud_rng.uniform(1.2, 2.5), 20), 2)
            balance_before = round(amount * fraud_rng.uniform(3, 6), 2)
            balance_after = round(balance_before - amount, 2)

        fraud_rows.append(
            {
                "id": f"T{txn_id:07d}",
                "user_id": profile.user_id,
                "amount": amount,
                "transaction_type": "TRANSFER",
                "origin_balance_before": balance_before,
                "origin_balance_after": balance_after,
                "location_country": location,
                "is_fraud_ground_truth": True,
                "occurred_at": occurred_at,
            }
        )
        txn_id += 1

    df = pd.concat([df, pd.DataFrame(fraud_rows)], ignore_index=True)
    df = df.sort_values("occurred_at").reset_index(drop=True)
    return df


def adapt_real_paysim(raw: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Best-effort adapter for the real Kaggle PaySim CSV -> our schema.

    Real PaySim has no user history/location, so profiles are still
    synthesized here. It also turns out real PaySim doesn't simulate
    returning customers at all -- ~99.9% of nameOrig values appear in
    exactly one transaction. That makes a per-user median amount degenerate
    (it's just that one transaction's own amount, so amount_to_typical_ratio
    is ~1.0 for almost everyone and the mule-pattern policy rule never
    fires). Users with too little history to form a real "typical" fall
    back to the population median for their transaction type instead --
    still profile-relative in spirit, just relative to a type-level norm
    rather than genuine personal history, which real PaySim doesn't have.
    """
    raw = raw.rename(
        columns={
            "nameOrig": "user_id",
            "type": "transaction_type",
            "oldbalanceOrg": "origin_balance_before",
            "newbalanceOrig": "origin_balance_after",
            "isFraud": "is_fraud_ground_truth",
        }
    )
    sim_start = pd.to_datetime("2024-06-01")
    raw["occurred_at"] = sim_start + pd.to_timedelta(raw["step"], unit="h")
    raw["is_fraud_ground_truth"] = raw["is_fraud_ground_truth"].astype(bool)

    user_ids = raw["user_id"].unique()
    home_country = rng.choice(COUNTRIES, size=len(user_ids))
    travel_frequency = rng.choice(TRAVEL_FREQUENCIES, size=len(user_ids), p=TRAVEL_WEIGHTS)
    account_created = pd.to_datetime("2024-01-01") - pd.to_timedelta(
        rng.integers(30, 365 * 4, size=len(user_ids)), unit="D"
    )
    country_map = dict(zip(user_ids, home_country))
    travel_map = dict(zip(user_ids, travel_frequency))

    # Vectorized -- looping .loc[] per user_id over millions of rows (an
    # earlier version of this function did exactly that) is far too slow.
    MIN_TXNS_FOR_PERSONAL_TYPICAL = 3
    type_typical = raw.groupby("transaction_type")["amount"].median()
    per_user = raw.groupby("user_id")["amount"].agg(["median", "count"]).reindex(user_ids)
    first_type = raw.groupby("user_id")["transaction_type"].first().reindex(user_ids)

    fallback_typical = first_type.map(type_typical).fillna(type_typical.median())
    has_enough_history = per_user["count"] >= MIN_TXNS_FOR_PERSONAL_TYPICAL
    typical_amount = per_user["median"].where(has_enough_history, fallback_typical).round(2)

    profiles = pd.DataFrame(
        {
            "user_id": user_ids,
            "account_created": account_created,
            "home_country": [country_map[u] for u in user_ids],
            "typical_transaction_amount": typical_amount.to_numpy(),
            "travel_frequency": [travel_map[u] for u in user_ids],
        }
    )
    raw["location_country"] = raw["user_id"].map(country_map)
    raw["id"] = [f"T{i:07d}" for i in range(len(raw))]

    cols = [
        "id",
        "user_id",
        "amount",
        "transaction_type",
        "origin_balance_before",
        "origin_balance_after",
        "location_country",
        "is_fraud_ground_truth",
        "occurred_at",
    ]
    return raw[cols], profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-users", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    if REAL_PAYSIM_PATH.exists():
        print(f"Found real PaySim CSV at {REAL_PAYSIM_PATH}, adapting it.")
        raw = pd.read_csv(REAL_PAYSIM_PATH)
        transactions, profiles = adapt_real_paysim(raw, rng)
    else:
        print("No real PaySim CSV found -- generating a synthetic stand-in.")
        profiles = generate_user_profiles(args.n_users, rng)
        transactions = simulate_transactions(profiles, rng)

    transactions.to_csv(DATA_DIR / "transactions.csv", index=False)
    profiles.to_csv(DATA_DIR / "user_profiles.csv", index=False)

    fraud_rate = transactions["is_fraud_ground_truth"].mean()
    print(f"Wrote {len(transactions)} transactions ({fraud_rate:.3%} fraud) "
          f"and {len(profiles)} user profiles to {DATA_DIR}")

    # policy_agent's "large reporting threshold" rule is meant to catch
    # genuinely unusual amounts. A fixed dollar figure doesn't transfer
    # across datasets of very different scale (real PaySim's amounts run
    # ~1000x our synthetic data's), so calibrate it here to this dataset's
    # own distribution instead of hardcoding it in policy_agent.py.
    large_reporting_threshold = round(float(transactions["amount"].quantile(0.995)), 2)
    calibration = {"large_reporting_threshold": large_reporting_threshold}
    models_dir = DATA_DIR.parent / "ml" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    with open(models_dir / "policy_calibration.json", "w") as f:
        json.dump(calibration, f, indent=2)
    print(f"Calibrated large_reporting_threshold = ${large_reporting_threshold:,.2f} "
          f"(99.5th percentile of amount) -> {models_dir / 'policy_calibration.json'}")


if __name__ == "__main__":
    main()