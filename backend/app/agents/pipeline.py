from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agents import anomaly_agent, context_agent, coordinator_agent, policy_agent
from app.models import AgentOpinion as AgentOpinionRow
from app.models import ReviewResult, Transaction, UserProfile
from app.schemas import TransactionReviewRequest


class UnknownUserError(ValueError):
    pass


def _profile_to_dict(profile: UserProfile) -> dict:
    return {
        "user_id": profile.user_id,
        "account_created": profile.account_created,
        "home_country": profile.home_country,
        "typical_transaction_amount": profile.typical_transaction_amount,
        "travel_frequency": profile.travel_frequency,
    }


def _transaction_to_dict(txn: Transaction) -> dict:
    return {
        "id": txn.id,
        "user_id": txn.user_id,
        "amount": txn.amount,
        "transaction_type": txn.transaction_type,
        "origin_balance_before": txn.origin_balance_before,
        "origin_balance_after": txn.origin_balance_after,
        "location_country": txn.location_country,
        "occurred_at": txn.occurred_at,
    }


def run_pipeline(db: Session, payload: TransactionReviewRequest) -> Transaction:
    profile = db.get(UserProfile, payload.user_id)
    if profile is None:
        raise UnknownUserError(f"No user_profile found for user_id={payload.user_id!r}")

    txn = Transaction(
        user_id=payload.user_id,
        amount=payload.amount,
        transaction_type=payload.transaction_type,
        origin_balance_before=payload.origin_balance_before,
        origin_balance_after=payload.origin_balance_after,
        location_country=payload.location_country,
        occurred_at=payload.occurred_at or datetime.now(timezone.utc).replace(tzinfo=None),
        is_fraud_ground_truth=payload.is_fraud_ground_truth,
    )
    db.add(txn)
    db.flush()  # assigns txn.id

    profile_dict = _profile_to_dict(profile)
    txn_dict = _transaction_to_dict(txn)
    account_age_days = max((txn.occurred_at.date() - profile.account_created).days, 0)

    policy_opinion = policy_agent.run(txn_dict, profile_dict, account_age_days)
    anomaly_opinion = anomaly_agent.run(txn_dict)
    context_opinion = context_agent.run(txn_dict, profile_dict)
    verdict = coordinator_agent.run(anomaly_opinion, context_opinion, policy_opinion)

    for opinion in (anomaly_opinion, context_opinion, policy_opinion):
        db.add(
            AgentOpinionRow(
                transaction_id=txn.id,
                agent_name=opinion.agent_name,
                score=opinion.score,
                flag=opinion.flag,
                reasoning=opinion.reasoning,
            )
        )
    db.add(
        ReviewResult(
            transaction_id=txn.id,
            final_verdict=verdict.final_verdict,
            coordinator_reasoning=verdict.coordinator_reasoning,
        )
    )

    db.commit()
    db.refresh(txn)
    return txn
