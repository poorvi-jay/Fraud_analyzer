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


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


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


def _make_review_result(db_session, sample_profile, final_verdict):
    """Builds a Transaction + ReviewResult directly (bypassing the pipeline)
    so override-endpoint tests can rely on a known verdict without depending
    on the anomaly model's/context heuristic's actual scoring behavior.
    """
    from datetime import datetime

    from app.models import ReviewResult, Transaction

    txn = Transaction(
        user_id=sample_profile.user_id,
        amount=500.0,
        transaction_type="PAYMENT",
        origin_balance_before=1000.0,
        origin_balance_after=500.0,
        location_country="US",
        occurred_at=datetime(2024, 6, 15, 10, 0, 0),
    )
    db_session.add(txn)
    db_session.flush()
    review_result = ReviewResult(
        transaction_id=txn.id,
        final_verdict=final_verdict,
        coordinator_reasoning="test fixture",
    )
    db_session.add(review_result)
    db_session.commit()
    db_session.refresh(review_result)
    return txn, review_result


@pytest.fixture
def escalated_case(db_session, sample_profile):
    return _make_review_result(db_session, sample_profile, "escalate")


@pytest.fixture
def allowed_case(db_session, sample_profile):
    return _make_review_result(db_session, sample_profile, "allow")


@pytest.fixture
def mock_reviewer():
    """Overrides the require_reviewer dependency so override-endpoint tests
    don't make real calls to Supabase's Auth API.
    """
    from app.auth import require_reviewer
    from app.main import app

    app.dependency_overrides[require_reviewer] = lambda: "test-reviewer-id"
    yield "test-reviewer-id"
    app.dependency_overrides.pop(require_reviewer, None)
