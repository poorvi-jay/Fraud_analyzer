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
    parser.add_argument("--yes", action="store_true", help="Skip the live-LLM cost confirmation.")
    args = parser.parse_args()

    from app.agents.context_agent import LLMConfigurationError
    from app.agents.pipeline import run_pipeline
    from app.config import settings
    from app.db import SessionLocal, init_db
    from app.llm_budget import flush, remaining
    from app.llm_usage import estimate_cost_usd, tracker
    from app.models import UserProfile
    from app.schemas import TransactionReviewRequest

    init_db()  # before the budget read below: creates llm_daily_spend if missing

    # Unlike ml/evaluate_baseline.py, seeding is NOT forced to the mock: the
    # seeded rows are what visitors actually read in the case queue, so a
    # demo seeded with the mock shows "[mock heuristic]" on every case. But
    # each row is one LLM call and the guaranteed-mix pass can search up to
    # --max-search candidates, so show the ceiling against the remaining
    # budget and confirm before spending. The per-call cap in context_agent
    # still applies: once the budget is hit, remaining rows are seeded with
    # the labelled mock rather than billed.
    if settings.llm_provider != "mock":
        worst_case = args.n_transactions + args.max_search
        est = estimate_cost_usd(settings.llm_model, worst_case)
        left_today, left_month = remaining()
        print(f"Seeding with LIVE {settings.llm_provider} ({settings.llm_model}): "
              f"{args.n_transactions} rows, up to {worst_case:,} LLM calls worst case "
              f"(~${est:.2f}). Typical runs use far fewer.")
        print(f"Budget left: ${left_today:.4f} today, ${left_month:.4f} this month.")
        if est > min(left_today, left_month):
            print("WARNING: the worst case exceeds the remaining budget. Rows seeded after "
                  "the cap is hit will use the labelled mock heuristic, so the demo queue "
                  "would be a mix of LLM and mock reasoning.")
        if not args.yes and input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("Aborted. Nothing was called, nothing was billed.")
            sys.exit(1)

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
                # float(): pandas columns are numpy float64, which psycopg2 has
                # no adapter for -- SQLite silently tolerated it, but against
                # real Postgres it gets embedded unquoted as literal text
                # ("np.float64(9482.19)"), which Postgres then tries to parse
                # as a schema-qualified function call and rejects.
                typical_transaction_amount=float(row.typical_transaction_amount),
                travel_frequency=row.travel_frequency,
            )
        )
        db.commit()
        return True

    def seed_transaction(row, when) -> str:
        payload = TransactionReviewRequest(
            user_id=row.user_id,
            amount=float(row.amount),
            transaction_type=row.transaction_type,
            origin_balance_before=float(row.origin_balance_before),
            origin_balance_after=float(row.origin_balance_after),
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
        except LLMConfigurationError:
            raise  # every remaining row would fail identically -- stop now
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
        except LLMConfigurationError:
            # Not a bad row -- the provider is misconfigured and every
            # remaining candidate would fail identically. Don't burn the
            # search budget pretending otherwise.
            raise
        except Exception:
            continue
        if verdict in found and found[verdict] < targets[verdict]:
            found[verdict] += 1

    for verdict, target in targets.items():
        status = "ok" if found[verdict] >= target else "SHORT -- ran out of search budget"
        print(f"Guaranteed-mix search: found {found[verdict]}/{target} {verdict} cases ({status}, searched {searched}).")

    flush()  # land every ledger write before reporting
    usage = tracker.totals
    if usage.calls:
        print(f"LLM usage: {usage.calls:,} calls, {usage.input_tokens:,} input + "
              f"{usage.output_tokens:,} output tokens, ${usage.cost_usd:.4f}.")

    db.close()


if __name__ == "__main__":
    main()
