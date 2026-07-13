"""Policy agent: deterministic rule checks, no ML/LLM involved.

Rules are illustrative, not derived from actual regulatory requirements
(see docs/PRD.md Non-goals) -- this is a portfolio project, not a compliance
system.
"""
from app.agents.base import AgentOpinion

LARGE_REPORTING_THRESHOLD = 10_000.0
MULE_DRAIN_MULTIPLE = 15.0
MULE_DRAIN_RATIO = 0.95
NEW_ACCOUNT_DAYS = 3
NEW_ACCOUNT_AMOUNT_FLOOR = 500.0


def run(transaction: dict, profile: dict, account_age_days: int) -> AgentOpinion:
    amount = float(transaction["amount"])
    typical = max(float(profile["typical_transaction_amount"]), 1.0)
    before = float(transaction["origin_balance_before"])
    after = float(transaction["origin_balance_after"])
    drained_ratio = (before - after) / max(before, 1.0)

    violations = []

    if (
        transaction["transaction_type"] in ("TRANSFER", "CASH_OUT")
        and amount >= typical * MULE_DRAIN_MULTIPLE
        and drained_ratio >= MULE_DRAIN_RATIO
    ):
        violations.append(
            f"full-balance drain ({drained_ratio:.0%}) via {transaction['transaction_type']} "
            f"at {amount / typical:.1f}x the account's typical amount -- classic mule pattern"
        )

    if amount >= LARGE_REPORTING_THRESHOLD:
        violations.append(f"amount ${amount:,.2f} exceeds the illustrative ${LARGE_REPORTING_THRESHOLD:,.0f} reporting threshold")

    if account_age_days < NEW_ACCOUNT_DAYS and amount > NEW_ACCOUNT_AMOUNT_FLOOR:
        violations.append(f"account is only {account_age_days} day(s) old and already moving ${amount:,.2f}")

    flag = len(violations) > 0
    reasoning = "; ".join(violations) if violations else "no policy violations found"
    return AgentOpinion(agent_name="policy_agent", score=1.0 if flag else 0.0, flag=flag, reasoning=reasoning)
