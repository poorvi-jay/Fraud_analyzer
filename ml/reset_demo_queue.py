"""
Delete every demo case from the database, so ml/seed_demo_queue.py can
rebuild the queue from scratch.

Why this exists: the live queue was seeded before the context agent had a
real LLM behind it, so every case carries "[mock heuristic]" reasoning that
no amount of re-seeding will update -- agent opinions are written once, when
the verdict is produced, and never regenerated. Clearing is the only way to
get a queue that reflects what the pipeline actually does now.

THIS IS DESTRUCTIVE AND IRREVERSIBLE. It is pointed at whatever DATABASE_URL
says, which in practice means production. It therefore:

  * prints the host it is about to delete from, so you can see it is the
    database you meant
  * prints exact row counts per table before touching anything
  * requires you to type the total row count back, not just "yes" -- a
    reflex "y" is exactly how the wrong database gets emptied

user_profiles are deliberately NOT deleted: seed_demo_queue.py reuses
existing profiles and only creates missing ones, so keeping them avoids
pointless churn and keeps any profile-relative history intact.

    python ml/reset_demo_queue.py

Then re-seed:

    python ml/seed_demo_queue.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AgentOpinion, HumanReview, ReviewResult, Transaction  # noqa: E402

# Child-first: human_reviews -> review_results -> agent_opinions ->
# transactions. Deleting in any other order trips a foreign key.
DELETE_ORDER = [HumanReview, ReviewResult, AgentOpinion, Transaction]


def _describe_target() -> str:
    """The database host, with any credentials stripped out."""
    url = settings.database_url
    if url.startswith("sqlite"):
        return f"SQLite file ({url.rsplit('/', 1)[-1]})"
    host = url.split("@")[-1].split("/")[0] if "@" in url else url.split("//")[-1].split("/")[0]
    return f"Postgres at {host}"


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        counts = {m.__tablename__: db.query(m).count() for m in DELETE_ORDER}
        total = sum(counts.values())

        print(f"Target: {_describe_target()}")
        print("\nRows to delete:")
        for table, n in counts.items():
            print(f"  {table:<16} {n:>7,}")
        print(f"  {'TOTAL':<16} {total:>7,}")

        if total == 0:
            print("\nNothing to delete.")
            return

        if counts["human_reviews"]:
            print(f"\nNOTE: {counts['human_reviews']} human review override(s) will be destroyed "
                  f"along with the cases they belong to. Those are demo records of the "
                  f"escalate -> human decision flow, and they cannot be recovered.")

        print(f"\nThis cannot be undone.")
        if input(f"Type the total ({total}) to confirm: ").strip() != str(total):
            print("Aborted. Nothing was deleted.")
            sys.exit(1)

        # One transaction: a partial delete would leave orphaned opinions
        # pointing at transactions that no longer exist.
        for model in DELETE_ORDER:
            deleted = db.query(model).delete(synchronize_session=False)
            print(f"  deleted {deleted:,} from {model.__tablename__}")
        db.commit()

        print(f"\nDone. Now run: python ml/seed_demo_queue.py")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
