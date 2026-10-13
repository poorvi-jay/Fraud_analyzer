from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# timeout: SQLite's busy handler is not fair, so a writer competing against a
# sustained stream of other writes can be starved. The spend ledger is written
# from a background thread while run_pipeline holds the write lock, and at the
# 5s default every write during a seed run was starved out and dropped. 30s is
# long enough to outlast a batch job's bursts; llm_budget also retries and
# never discards an unwritten amount.
connect_args = (
    {"check_same_thread": False, "timeout": 30.0}
    if settings.database_url.startswith("sqlite")
    else {}
)
engine_kwargs = {}
if not settings.database_url.startswith("sqlite"):
    # Supabase fronts Postgres with a connection pooler that drops idle or
    # long-lived connections, which surfaces as "server closed the connection
    # unexpectedly" mid-run. pool_pre_ping tests a pooled connection before
    # handing it out and transparently replaces a dead one; pool_recycle
    # retires connections before the pooler decides to. Without these, a long
    # batch job (ml/seed_demo_queue.py) reliably dies partway through.
    engine_kwargs = {"pool_pre_ping": True, "pool_recycle": 300}

engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)

    # create_all never enables RLS. On Supabase, a table in the public schema
    # without RLS is readable AND writable through the REST API by anyone
    # holding the anon key -- which ships in the frontend bundle. For the
    # spend ledger that would let a visitor zero out spend to bypass the cap.
    # Enable it here so safety doesn't depend on someone remembering to run
    # supabase/schema.sql before the first deploy. Idempotent; no-op on SQLite.
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("alter table llm_daily_spend enable row level security"))
