"""
Seeds the local database with user profiles and a batch of "live" demo
transactions (no ground truth, since real demo traffic has none) so the
case queue isn't empty on first load.

Calls the pipeline directly against the DB (not the HTTP API) -- seeding is
an internal/admin operation, not the public demo flow the review endpoint's
rate limit is meant to protect.

Escalate/block base rates on this dataset are low (~2% and ~1%, see
ml/reports/baseline_comparison.md), so a pure random sample this small
often comes back 100% "allow" -- which leaves the live demo queue never
showing the escalate -> human override flow it exists to demonstrate.
After seeding the random sample, this script keeps drawing additional
(profile, transaction) pairs -- seeding each profile on demand, since
this dataset is close to one transaction per user -- until it's found at
least --min-escalate escalate cases and --min-block block cases, or hits
--max-search candidates tried.

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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-transactions", type=int, default=60, help="Size of the primary random sample.")
    parser.add_argument("--min-escalate", type=int, default=3, help="Guaranteed minimum escalate cases in the queue.")
    parser.add_argument("--min-block", type=int, default=3, help="Guaranteed minimum block cases in the queue.")
    parser.add_argument("--max-search", type=int, default=1000, help="Search budget for the guaranteed-mix pass.")
    args = parser.parse_args()

    from app.agents.pipeline import run_pipeline
    from app.db import SessionLocal, init_db
    from app.models import UserProfile
    from app.schemas import TransactionReviewRequest

    init_db()
    db = SessionLocal()

    profiles = pd.read_csv(DATA_DIR / "user_profiles.csv").set_index("user_id")
    transactions = pd.read_csv(DATA_DIR / "transactions.csv")
    transactions = transactions[transactions["user_id"].isin(profiles.index)]

    def ensure_profile(user_id: str) -> bool:
        if db.get(UserProfile, user_id) is not None:
            return False
        row = profiles.loc[user_id]
        db.add(
            UserProfile(
                user_id=user_id,
                account_created=pd.to_datetime(row.account_created).date(),
                home_country=row.home_country,
                typical_transaction_amount=row.typical_transaction_amount,
                travel_frequency=row.travel_frequency,
            )
        )
        db.commit()
        return True

    def seed_transaction(row, when) -> str:
        payload = TransactionReviewRequest(
            user_id=row.user_id,
            amount=row.amount,
            transaction_type=row.transaction_type,
            origin_balance_before=row.origin_balance_before,
            origin_balance_after=row.origin_balance_after,
            location_country=row.location_country,
            # Ground truth intentionally omitted -- live demo traffic has none.
            occurred_at=when,
        )
        txn = run_pipeline(db, payload)
        return txn.review_result.final_verdict

    now = datetime.utcnow()

    # 1. Primary random sample -- the bulk of the queue, spread across
    # recent hours so it looks "live".
    primary = transactions.sample(n=min(args.n_transactions, len(transactions)), random_state=7)
    seeded_profiles = 0
    ok = 0
    verdict_counts = {"allow": 0, "escalate": 0, "block": 0}
    for i, row in enumerate(primary.itertuples(index=False)):
        if ensure_profile(row.user_id):
            seeded_profiles += 1
        try:
            verdict = seed_transaction(row, now - timedelta(hours=len(primary) - i))
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            ok += 1
        except Exception as exc:
            print(f"  failed for {row.user_id}: {exc}")

    print(f"Seeded {seeded_profiles} new user profiles.")
    print(f"Reviewed {ok}/{len(primary)} demo transactions (random sample): {verdict_counts}")

    # 2. Guaranteed-mix pass: keep searching fresh candidates until the
    # minimums are met (or the search budget runs out). Every transaction
    # tried here gets persisted regardless of its verdict -- "allow" ones
    # just add a bit more realistic volume alongside the guaranteed cases.
    pool = transactions.drop(index=primary.index).sample(frac=1, random_state=13)
    targets = {"escalate": args.min_escalate, "block": args.min_block}
    found = {"escalate": 0, "block": 0}
    searched = 0
    for row in pool.itertuples(index=False):
        if found["escalate"] >= targets["escalate"] and found["block"] >= targets["block"]:
            break
        if searched >= args.max_search:
            break
        searched += 1
        ensure_profile(row.user_id)
        try:
            verdict = seed_transaction(row, now - timedelta(minutes=searched))
        except Exception:
            continue
        if verdict in found and found[verdict] < targets[verdict]:
            found[verdict] += 1

    for verdict, target in targets.items():
        status = "ok" if found[verdict] >= target else "SHORT -- ran out of search budget"
        print(f"Guaranteed-mix search: found {found[verdict]}/{target} {verdict} cases ({status}, searched {searched}).")

    db.close()


if __name__ == "__main__":
    main()
