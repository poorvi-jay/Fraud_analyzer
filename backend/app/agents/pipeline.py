from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.adapters import razorpay_adapter
from app.agents import anomaly_agent, context_agent, coordinator_agent, policy_agent
from app.agents.base import AgentOpinion
from app.agents.coordinator_agent import Verdict
from app.models import AgentOpinion as AgentOpinionRow
from app.models import ReviewResult, Transaction, UserProfile
from app.schemas import RazorpaySimulateRequest, SimulateRequest, TransactionReviewRequest


class UnknownUserError(ValueError):
    pass


class RazorpayIngestionError(ValueError):
    """The Razorpay payload can't be adapted without inventing data."""


class RazorpayDegradedModeError(ValueError):
    """Adaptation would drop model features, and the caller didn't opt in."""


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

    # Everything from here to commit() is wrapped so a mid-pipeline failure
    # (an agent throwing) rolls back the flushed Transaction row instead of
    # leaving it pending on the session. A single get_db()-scoped request
    # would have this cleaned up by the session close anyway, but any caller
    # that reuses one session across many calls (e.g. ml/seed_demo_queue.py)
    # would otherwise have that orphaned row silently swept into whatever
    # unrelated commit() happens next -- a transaction with no opinions and
    # no review result ever attached.
    try:
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
    except Exception:
        db.rollback()
        raise

    db.refresh(txn)
    return txn


@dataclass(frozen=True)
class SimulationResult:
    anomaly: AgentOpinion
    context: AgentOpinion
    policy: AgentOpinion
    verdict: Verdict


@dataclass(frozen=True)
class RazorpaySimulationResult:
    """A SimulationResult plus the adapter's account of what it had to drop
    getting there -- so a caller can never read the verdict without also
    being handed the feature availability it rests on.
    """

    simulation: SimulationResult
    adapted: "razorpay_adapter.AdaptedPayment"


def run_simulation(db: Session, payload: SimulateRequest) -> SimulationResult:
    """Playground path: runs the same three agents + coordinator as
    run_pipeline, but never touches the database -- no Transaction,
    AgentOpinion, or ReviewResult row is ever created. Lets a visitor try an
    arbitrary user_id/profile combination without polluting the demo queue.
    """
    occurred_at = payload.occurred_at or datetime.now(timezone.utc).replace(tzinfo=None)

    if payload.profile is not None:
        profile_dict = {
            "user_id": payload.user_id or "sandbox",
            "account_created": occurred_at.date() - timedelta(days=payload.profile.account_age_days),
            "home_country": payload.profile.home_country,
            "typical_transaction_amount": payload.profile.typical_transaction_amount,
            "travel_frequency": payload.profile.travel_frequency,
        }
    else:
        profile = db.get(UserProfile, payload.user_id)
        if profile is None:
            raise UnknownUserError(f"No user_profile found for user_id={payload.user_id!r}")
        profile_dict = _profile_to_dict(profile)

    txn_dict = {
        "amount": payload.amount,
        "transaction_type": payload.transaction_type,
        "origin_balance_before": payload.origin_balance_before,
        "origin_balance_after": payload.origin_balance_after,
        "location_country": payload.location_country,
        "occurred_at": occurred_at,
    }
    account_age_days = max((occurred_at.date() - profile_dict["account_created"]).days, 0)

    return run_agents(txn_dict, profile_dict, account_age_days)


def run_agents(
    txn_dict: dict,
    profile_dict: dict,
    account_age_days: int,
    *,
    anomaly_override: AgentOpinion | None = None,
) -> SimulationResult:
    """The four-agent step on its own, factored out so alternative ingestion
    paths (see app/adapters/) reuse the exact same orchestration instead of
    reimplementing it.

    anomaly_override substitutes a caller-supplied opinion for
    anomaly_agent.run(). It exists for one specific case: an ingestion source
    that cannot supply the balance fields the model needs, where the honest
    move is to not run the model at all rather than run it on invented
    inputs. anomaly_agent itself is untouched by this -- the override is
    constructed by the adapter and clearly labelled as an absent score. The
    coordinator's table is likewise untouched; it sees a normal
    AgentOpinion and applies the same five rules.
    """
    policy_opinion = policy_agent.run(txn_dict, profile_dict, account_age_days)
    anomaly_opinion = anomaly_override if anomaly_override is not None else anomaly_agent.run(txn_dict)
    context_opinion = context_agent.run(txn_dict, profile_dict)
    verdict = coordinator_agent.run(anomaly_opinion, context_opinion, policy_opinion)

    return SimulationResult(anomaly=anomaly_opinion, context=context_opinion, policy=policy_opinion, verdict=verdict)


def run_razorpay_simulation(db: Session, payload: RazorpaySimulateRequest) -> "RazorpaySimulationResult":
    """Razorpay ingestion path: adapt a Razorpay Payment object onto the
    internal schema, then run the same four agents as every other path.

    Profile resolution mirrors run_simulation: an inline profile if the
    caller supplied one, otherwise the seeded user_profiles row for the
    identity the adapter derived from the payment.
    """
    payment = payload.payment.model_dump()

    try:
        user_id, _ = razorpay_adapter.resolve_user_id(payment)
    except razorpay_adapter.RazorpayAdapterError as exc:
        raise RazorpayIngestionError(str(exc)) from exc

    if payload.profile is not None:
        home_country = payload.profile.home_country
    else:
        profile_row = db.get(UserProfile, user_id)
        if profile_row is None:
            raise UnknownUserError(
                f"No user_profile found for the identity derived from this payment "
                f"(user_id={user_id!r}). Razorpay does not serve per-customer spend history, so "
                f"amount_to_typical_ratio has no source unless this customer already has a "
                f"profile row or you supply an inline profile."
            )
        home_country = profile_row.home_country

    balance_context = (
        razorpay_adapter.BalanceContext(
            origin_balance_before=payload.balance_context.origin_balance_before,
            origin_balance_after=payload.balance_context.origin_balance_after,
        )
        if payload.balance_context is not None
        else None
    )

    try:
        adapted = razorpay_adapter.adapt_payment(
            payment, home_country=home_country, balance_context=balance_context
        )
    except razorpay_adapter.RazorpayAdapterError as exc:
        raise RazorpayIngestionError(str(exc)) from exc

    if not adapted.anomaly_scorable and not payload.allow_degraded:
        raise RazorpayDegradedModeError(
            "This payment has no balance context, so 3 of the anomaly model's 9 features "
            "(origin_balance_before, origin_balance_after, balance_drained_ratio) have no "
            "source. Rather than default them to values the model never saw in training, this "
            "request is refused. Either supply balance_context from your own ledger, or resend "
            "with allow_degraded=true to score on the context and policy agents alone -- the "
            "response will say so explicitly."
        )

    occurred_at = adapted.transaction["occurred_at"]
    if payload.profile is not None:
        profile_dict = {
            "user_id": adapted.user_id,
            "account_created": occurred_at.date() - timedelta(days=payload.profile.account_age_days),
            "home_country": payload.profile.home_country,
            "typical_transaction_amount": payload.profile.typical_transaction_amount,
            "travel_frequency": payload.profile.travel_frequency,
        }
    else:
        profile_dict = _profile_to_dict(profile_row)

    account_age_days = max((occurred_at.date() - profile_dict["account_created"]).days, 0)

    if adapted.anomaly_scorable:
        anomaly_override = None
        agent_txn = adapted.transaction
    else:
        anomaly_override = razorpay_adapter.unavailable_anomaly_opinion(
            "Razorpay's Payment object carries no account balance, and no balance_context was "
            "supplied, so origin_balance_before / origin_balance_after / balance_drained_ratio "
            "were unavailable."
        )
        # policy_agent coerces the balance fields unconditionally and would
        # raise on None. policy_safe_view substitutes values that the one
        # balance-reading rule provably cannot reach; context_agent reads no
        # balances at all, and anomaly_agent isn't running. See the docstring
        # there for why this is not the same thing as defaulting the feature.
        agent_txn = razorpay_adapter.policy_safe_view(adapted.transaction)

    result = run_agents(
        agent_txn, profile_dict, account_age_days, anomaly_override=anomaly_override
    )
    return RazorpaySimulationResult(simulation=result, adapted=adapted)
