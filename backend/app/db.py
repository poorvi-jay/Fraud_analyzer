from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
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
