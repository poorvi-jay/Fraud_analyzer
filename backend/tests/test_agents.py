from datetime import date, datetime

from app.agents import anomaly_agent, context_agent, coordinator_agent, policy_agent
from app.agents.base import AgentOpinion

PROFILE = {
    "user_id": "C_TEST",
    "account_created": date(2020, 1, 1),
    "home_country": "US",
    "typical_transaction_amount": 200.0,
    "travel_frequency": "never",
}


def _txn(**overrides):
    base = {
        "amount": 150.0,
        "transaction_type": "PAYMENT",
        "origin_balance_before": 1000.0,
        "origin_balance_after": 850.0,
        "location_country": "US",
        "occurred_at": datetime(2024, 6, 15, 10, 0, 0),
    }
    base.update(overrides)
    return base


class TestPolicyAgent:
    def test_no_violation_for_ordinary_transaction(self):
        opinion = policy_agent.run(_txn(), PROFILE, account_age_days=1000)
        assert opinion.flag is False
        assert "no policy violations" in opinion.reasoning

    def test_flags_full_balance_drain_mule_pattern(self):
        txn = _txn(
            amount=4000.0,
            transaction_type="TRANSFER",
            origin_balance_before=4000.0,
            origin_balance_after=0.0,
        )
        opinion = policy_agent.run(txn, PROFILE, account_age_days=1000)
        assert opinion.flag is True
        assert "mule pattern" in opinion.reasoning

    def test_flags_large_reporting_threshold(self):
        txn = _txn(amount=15000.0, origin_balance_before=20000.0, origin_balance_after=5000.0)
        opinion = policy_agent.run(txn, PROFILE, account_age_days=1000)
        assert opinion.flag is True
        assert "reporting threshold" in opinion.reasoning

    def test_flags_new_account_large_transaction(self):
        opinion = policy_agent.run(_txn(amount=600.0), PROFILE, account_age_days=1)
        assert opinion.flag is True
        assert "day(s) old" in opinion.reasoning


class TestAnomalyAgent:
    def test_ordinary_transaction_scores_low(self):
        opinion = anomaly_agent.run(_txn())
        assert opinion.agent_name == "anomaly_agent"
        assert 0.0 <= opinion.score <= 1.0

    def test_large_drain_scores_higher_than_ordinary(self):
        ordinary = anomaly_agent.run(_txn())
        drained = anomaly_agent.run(
            _txn(amount=5000.0, transaction_type="TRANSFER", origin_balance_before=5000.0, origin_balance_after=0.0)
        )
        assert drained.score > ordinary.score


class TestContextAgentMock:
    def test_home_country_typical_amount_is_plausible(self):
        opinion = context_agent.run(_txn(), PROFILE)
        assert opinion.flag is False

    def test_foreign_transaction_for_never_traveller_is_implausible(self):
        opinion = context_agent.run(_txn(location_country="FR"), PROFILE)
        assert opinion.flag is True

    def test_foreign_large_amount_plausible_for_frequent_traveller(self):
        frequent_profile = {**PROFILE, "travel_frequency": "frequent"}
        txn = _txn(location_country="FR", amount=200.0 * 8)
        opinion = context_agent.run(txn, frequent_profile)
        assert opinion.flag is False


class TestCoordinatorAgent:
    def _opinion(self, name, flag):
        return AgentOpinion(agent_name=name, score=0.9 if flag else 0.1, flag=flag, reasoning="test")

    def test_policy_violation_always_blocks(self):
        verdict = coordinator_agent.run(
            self._opinion("anomaly_agent", False),
            self._opinion("context_agent", False),
            self._opinion("policy_agent", True),
        )
        assert verdict.final_verdict == "block"

    def test_high_anomaly_implausible_context_blocks(self):
        verdict = coordinator_agent.run(
            self._opinion("anomaly_agent", True),
            self._opinion("context_agent", True),
            self._opinion("policy_agent", False),
        )
        assert verdict.final_verdict == "block"

    def test_high_anomaly_plausible_context_escalates(self):
        verdict = coordinator_agent.run(
            self._opinion("anomaly_agent", True),
            self._opinion("context_agent", False),
            self._opinion("policy_agent", False),
        )
        assert verdict.final_verdict == "escalate"

    def test_low_anomaly_implausible_context_escalates(self):
        verdict = coordinator_agent.run(
            self._opinion("anomaly_agent", False),
            self._opinion("context_agent", True),
            self._opinion("policy_agent", False),
        )
        assert verdict.final_verdict == "escalate"

    def test_low_anomaly_plausible_context_allows(self):
        verdict = coordinator_agent.run(
            self._opinion("anomaly_agent", False),
            self._opinion("context_agent", False),
            self._opinion("policy_agent", False),
        )
        assert verdict.final_verdict == "allow"
