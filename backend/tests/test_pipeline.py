from datetime import datetime

import pytest

from app.agents.pipeline import UnknownUserError, run_pipeline
from app.schemas import TransactionReviewRequest


def test_run_pipeline_persists_opinions_and_verdict(db_session, sample_profile):
    payload = TransactionReviewRequest(
        user_id=sample_profile.user_id,
        amount=180.0,
        transaction_type="PAYMENT",
        origin_balance_before=1000.0,
        origin_balance_after=820.0,
        location_country="US",
        occurred_at=datetime(2024, 6, 15, 10, 0, 0),
    )
    txn = run_pipeline(db_session, payload)

    assert txn.id is not None
    assert {o.agent_name for o in txn.opinions} == {"anomaly_agent", "context_agent", "policy_agent"}
    assert txn.review_result is not None
    assert txn.review_result.final_verdict in ("allow", "escalate", "block")


def test_run_pipeline_unknown_user_raises(db_session):
    payload = TransactionReviewRequest(
        user_id="does-not-exist",
        amount=100.0,
        transaction_type="PAYMENT",
        origin_balance_before=500.0,
        origin_balance_after=400.0,
        location_country="US",
    )
    with pytest.raises(UnknownUserError):
        run_pipeline(db_session, payload)


def test_run_pipeline_obvious_fraud_pattern_blocks(db_session, sample_profile):
    payload = TransactionReviewRequest(
        user_id=sample_profile.user_id,
        amount=8000.0,
        transaction_type="TRANSFER",
        origin_balance_before=8000.0,
        origin_balance_after=0.0,
        location_country="FR",
        occurred_at=datetime(2024, 6, 15, 10, 0, 0),
    )
    txn = run_pipeline(db_session, payload)
    assert txn.review_result.final_verdict == "block"
