"""
Seeds the local database with user profiles and a batch of "live" demo
transactions (no ground truth, since real demo traffic has none) so the
case queue isn't empty on first load.

Calls the pipeline directly against the DB (not the HTTP API) -- seeding is
an internal/admin operation, not the public demo flow the review endpoint's
rate limit is meant to protect.

    python ml/seed_demo_queue.py
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-users", type=int, default=40)
    parser.add_argument("--n-transactions", type=int, default=60)
    args = parser.parse_args()

    from app.agents.pipeline import run_pipeline
    from app.db import SessionLocal, init_db
    from app.models import UserProfile
    from app.schemas import TransactionReviewRequest

    init_db()
    db = SessionLocal()

    profiles = pd.read_csv(DATA_DIR / "user_profiles.csv")
    transactions = pd.read_csv(DATA_DIR / "transactions.csv")

    sampled_profiles = profiles.sample(n=min(args.n_users, len(profiles)), random_state=7)
    seeded = 0
    for row in sampled_profiles.itertuples(index=False):
        if db.get(UserProfile, row.user_id) is not None:
            continue
        db.add(
            UserProfile(
                user_id=row.user_id,
                account_created=pd.to_datetime(row.account_created).date(),
                home_country=row.home_country,
                typical_transaction_amount=row.typical_transaction_amount,
                travel_frequency=row.travel_frequency,
            )
        )
        seeded += 1
    db.commit()
    print(f"Seeded {seeded} new user profiles ({len(sampled_profiles) - seeded} already existed).")

    candidates = transactions[transactions["user_id"].isin(sampled_profiles["user_id"])]
    sample = candidates.sample(n=min(args.n_transactions, len(candidates)), random_state=7)

    now = datetime.utcnow()
    ok = 0
    for i, row in enumerate(sample.itertuples(index=False)):
        payload = TransactionReviewRequest(
            user_id=row.user_id,
            amount=row.amount,
            transaction_type=row.transaction_type,
            origin_balance_before=row.origin_balance_before,
            origin_balance_after=row.origin_balance_after,
            location_country=row.location_country,
            # Spread across recent hours so the queue looks "live"; ground
            # truth intentionally omitted -- live demo traffic has none.
            occurred_at=now - timedelta(hours=len(sample) - i),
        )
        try:
            run_pipeline(db, payload)
            ok += 1
        except Exception as exc:
            print(f"  failed for {row.user_id}: {exc}")

    db.close()
    print(f"Reviewed {ok}/{len(sample)} demo transactions into the local DB.")


if __name__ == "__main__":
    main()
