import os
from datetime import date
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent / "test_fraud_analyzer.db"
# Must be set before the first `from app... import` anywhere, since
# app.config.Settings() is evaluated at import time.
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["REVIEW_RATE_LIMIT"] = "1000/minute"  # avoid cross-test flakiness from the real demo rate limit

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    from app.db import init_db

    init_db()
    yield
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture
def db_session():
    from app.db import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def sample_profile(db_session):
    from app.models import UserProfile

    profile = UserProfile(
        user_id="C_FIXTURE",
        account_created=date(2022, 1, 1),
        home_country="US",
        typical_transaction_amount=200.0,
        travel_frequency="never",
    )
    existing = db_session.get(UserProfile, "C_FIXTURE")
    if existing is None:
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        return profile
    return existing
