"""Tests for the cost guards around live LLM calls.

Nothing here makes a real API call: the provider call is monkeypatched, so
every assertion is about *our* logic -- pricing, the spend ledger, the
daily/monthly caps, and which failures are allowed to fall back to the mock
heuristic rather than surface loudly.

That distinction is the point of most of these tests. A silent fallback on
a bad key means running the mock while believing the demo is LLM-backed,
which is exactly the failure this suite exists to prevent regressing.
"""
from datetime import date, datetime, timezone

import pytest

from app.agents import context_agent
from app.agents.context_agent import LLMConfigurationError
from app.config import settings
from app.llm_budget import LLMBudgetExceeded, check_budget, flush, remaining
from app.llm_usage import (
    UnknownModelPricingError,
    cost_usd,
    estimate_cost_usd,
    price_for,
    tracker,
)

MODEL = "gpt-5.6-luna"

PROFILE = {
    "user_id": "C_BUDGET",
    "account_created": date(2020, 1, 1),
    "home_country": "US",
    "typical_transaction_amount": 200.0,
    "travel_frequency": "never",
}
TXN = {
    "amount": 150.0,
    "transaction_type": "PAYMENT",
    "origin_balance_before": 1000.0,
    "origin_balance_after": 850.0,
    "location_country": "US",
    "occurred_at": datetime(2024, 6, 15, 10, 0, 0),
}

GOOD_RESPONSE = '{"plausible": true, "reasoning": "Consistent with profile."}'


def _utc_today() -> date:
    """The ledger keys rows by UTC date. Using date.today() here would be
    flaky in any timezone offset from UTC -- in IST it is wrong for the
    first 5.5 hours of each local day."""
    return datetime.now(timezone.utc).date()


class ProviderError(Exception):
    """Stand-in for an SDK error carrying an HTTP status, which is what
    _is_configuration_error branches on."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture
def clean_ledger(db_session):
    """Empty the spend ledger before and after, so cap tests start from $0."""
    from app.models import LLMDailySpend

    def _clear():
        db_session.query(LLMDailySpend).delete()
        db_session.commit()

    _clear()
    tracker.reset()
    yield
    _clear()
    tracker.reset()


@pytest.fixture
def live_openai(monkeypatch):
    """Configure a working 'openai' provider with generous budgets."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-a-real-key")
    monkeypatch.setattr(settings, "llm_model", MODEL)
    monkeypatch.setattr(settings, "llm_daily_budget_usd", 1.0)
    monkeypatch.setattr(settings, "llm_monthly_budget_usd", 10.0)


def _stub_call(monkeypatch, text=GOOD_RESPONSE, input_tokens=260, output_tokens=55):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return text, input_tokens, output_tokens

    monkeypatch.setattr(context_agent, "_call_openai", fake)
    return calls


def _stub_raising(monkeypatch, exc):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        raise exc

    monkeypatch.setattr(context_agent, "_call_openai", fake)
    return calls


class TestPricing:
    def test_cost_matches_published_rates(self):
        # 1M input + 1M output at Luna's $0.20 / $1.20.
        assert cost_usd(MODEL, 1_000_000, 1_000_000) == pytest.approx(1.40)

    def test_per_call_estimate_is_a_fraction_of_a_cent(self):
        assert estimate_cost_usd(MODEL, 1) == pytest.approx(0.000118, abs=1e-6)

    def test_estimate_scales_linearly(self):
        assert estimate_cost_usd(MODEL, 1000) == pytest.approx(estimate_cost_usd(MODEL, 1) * 1000)

    def test_unknown_model_raises_rather_than_guessing(self):
        with pytest.raises(UnknownModelPricingError):
            price_for("gpt-5.6-luna-20260730")


class TestConfigurationFailsLoudly:
    """These must NOT fall back to the mock: they are wrong on every call."""

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "")
        with pytest.raises(LLMConfigurationError, match="no API key"):
            context_agent.run(TXN, PROFILE)

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "gemini")
        with pytest.raises(LLMConfigurationError, match="not a known provider"):
            context_agent.run(TXN, PROFILE)

    def test_unpriceable_model_raises_and_names_the_duplicate_line_trap(self, monkeypatch):
        """The exact foot-gun that bit this project: a stale duplicate
        LLM_MODEL line in .env silently wins over the intended one."""
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_model", "claude-haiku-4-5-20251001")
        with pytest.raises(LLMConfigurationError) as exc:
            context_agent.run(TXN, PROFILE)
        assert "no pricing entry" in str(exc.value)
        assert "LAST one" in str(exc.value)

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_auth_and_unknown_model_statuses_raise(self, monkeypatch, live_openai, clean_ledger, status):
        _stub_raising(monkeypatch, ProviderError("nope", status))
        with pytest.raises(LLMConfigurationError):
            context_agent.run(TXN, PROFILE)

    def test_bad_request_naming_the_model_raises(self, monkeypatch, live_openai, clean_ledger):
        _stub_raising(monkeypatch, ProviderError("unknown model foo", 400))
        with pytest.raises(LLMConfigurationError):
            context_agent.run(TXN, PROFILE)


class TestTransientFailsSoft:
    """These SHOULD fall back -- one blip shouldn't kill a scoring request."""

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_fall_back_labelled(self, monkeypatch, live_openai, clean_ledger, status):
        _stub_raising(monkeypatch, ProviderError("busy", status))
        opinion = context_agent.run(TXN, PROFILE)
        assert "fell back to mock heuristic" in opinion.reasoning

    def test_unparseable_output_falls_back_labelled(self, monkeypatch, live_openai, clean_ledger):
        _stub_call(monkeypatch, text="not json at all")
        opinion = context_agent.run(TXN, PROFILE)
        assert "unparseable" in opinion.reasoning
        assert "fell back to mock heuristic" in opinion.reasoning

    def test_unparseable_output_is_still_billed(self, monkeypatch, live_openai, clean_ledger):
        """The call happened; the money is gone regardless of the output."""
        _stub_call(monkeypatch, text="not json at all")
        context_agent.run(TXN, PROFILE)
        flush()
        assert tracker.totals.calls == 1


class TestLedger:
    def test_successful_call_is_recorded(self, monkeypatch, live_openai, clean_ledger, db_session):
        from app.models import LLMDailySpend

        _stub_call(monkeypatch, input_tokens=260, output_tokens=55)
        opinion = context_agent.run(TXN, PROFILE)
        assert opinion.reasoning == "Consistent with profile."
        flush()
        db_session.rollback()

        row = db_session.get(LLMDailySpend, _utc_today())
        db_session.refresh(row)
        assert row.calls == 1
        assert row.input_tokens == 260
        assert row.output_tokens == 55
        assert row.cost_usd == pytest.approx(cost_usd(MODEL, 260, 55))

    def test_repeated_calls_accumulate(self, monkeypatch, live_openai, clean_ledger, db_session):
        from app.models import LLMDailySpend

        _stub_call(monkeypatch)
        for _ in range(3):
            context_agent.run(TXN, PROFILE)
        flush()
        db_session.rollback()

        row = db_session.get(LLMDailySpend, _utc_today())
        db_session.refresh(row)
        assert row.calls == 3
        assert row.input_tokens == 780

    def test_mock_provider_writes_nothing(self, monkeypatch, clean_ledger, db_session):
        from app.models import LLMDailySpend

        monkeypatch.setattr(settings, "llm_provider", "mock")
        context_agent.run(TXN, PROFILE)
        flush()
        db_session.rollback()
        assert db_session.query(LLMDailySpend).count() == 0


class TestCaps:
    def _spend(self, db_session, amount):
        from app.models import LLMDailySpend

        db_session.add(LLMDailySpend(day=_utc_today(), calls=1, input_tokens=0,
                                     output_tokens=0, cost_usd=amount))
        db_session.commit()

    def test_under_budget_passes(self, monkeypatch, live_openai, clean_ledger):
        check_budget(MODEL)  # must not raise

    def test_daily_cap_blocks(self, monkeypatch, live_openai, clean_ledger, db_session):
        monkeypatch.setattr(settings, "llm_daily_budget_usd", 0.05)
        self._spend(db_session, 0.05)
        with pytest.raises(LLMBudgetExceeded, match="daily"):
            check_budget(MODEL)

    def test_monthly_cap_blocks(self, monkeypatch, live_openai, clean_ledger, db_session):
        monkeypatch.setattr(settings, "llm_daily_budget_usd", 100.0)
        monkeypatch.setattr(settings, "llm_monthly_budget_usd", 0.05)
        self._spend(db_session, 0.05)
        with pytest.raises(LLMBudgetExceeded, match="monthly"):
            check_budget(MODEL)

    def test_cap_counts_the_call_about_to_be_made(self, monkeypatch, live_openai, clean_ledger, db_session):
        """Spend sits just under the cap, but one more call crosses it, so
        the check must refuse rather than allow the overshoot."""
        monkeypatch.setattr(settings, "llm_daily_budget_usd", 0.05)
        self._spend(db_session, 0.05 - estimate_cost_usd(MODEL, 1) / 2)
        with pytest.raises(LLMBudgetExceeded):
            check_budget(MODEL)

    def test_bulk_check_refuses_a_run_that_would_not_fit(self, monkeypatch, live_openai, clean_ledger):
        """What stops `evaluate_baseline --allow-llm --sample-size 0`."""
        monkeypatch.setattr(settings, "llm_daily_budget_usd", 0.05)
        with pytest.raises(LLMBudgetExceeded):
            check_budget(MODEL, n_calls=1_590_000)

    def test_remaining_reports_headroom_and_floors_at_zero(self, monkeypatch, live_openai, clean_ledger, db_session):
        monkeypatch.setattr(settings, "llm_daily_budget_usd", 0.10)
        monkeypatch.setattr(settings, "llm_monthly_budget_usd", 1.00)
        self._spend(db_session, 0.25)  # over the daily cap, under the monthly
        left_today, left_month = remaining()
        assert left_today == 0.0
        assert left_month == pytest.approx(0.75)

    def test_exhausted_budget_falls_back_without_calling(self, monkeypatch, live_openai, clean_ledger, db_session):
        monkeypatch.setattr(settings, "llm_daily_budget_usd", 0.01)
        self._spend(db_session, 0.01)
        calls = _stub_call(monkeypatch)

        opinion = context_agent.run(TXN, PROFILE)

        assert calls == [], "budget was exhausted but the provider was still called"
        assert "LLM budget reached" in opinion.reasoning
        assert "mock heuristic" in opinion.reasoning

    def test_fails_closed_when_the_ledger_cannot_be_read(self, monkeypatch, live_openai):
        """A ledger we can't read means a cap we can't enforce, so no call."""
        import app.llm_budget as budget

        def boom():
            raise RuntimeError("database is gone")

        monkeypatch.setattr(budget, "spend_snapshot", boom)
        with pytest.raises(LLMBudgetExceeded, match="failing closed"):
            check_budget(MODEL)

    def test_unreadable_ledger_degrades_to_mock_without_calling(self, monkeypatch, live_openai):
        import app.llm_budget as budget

        def boom():
            raise RuntimeError("database is gone")

        monkeypatch.setattr(budget, "spend_snapshot", boom)
        calls = _stub_call(monkeypatch)
        opinion = context_agent.run(TXN, PROFILE)
        assert calls == []
        assert "mock heuristic" in opinion.reasoning


class TestUsageEndpoint:
    def test_reports_spend_against_the_caps(self, client, monkeypatch, live_openai, clean_ledger, db_session):
        from app.models import LLMDailySpend

        monkeypatch.setattr(settings, "llm_daily_budget_usd", 0.15)
        monkeypatch.setattr(settings, "llm_monthly_budget_usd", 1.00)
        db_session.add(LLMDailySpend(day=_utc_today(), calls=2, input_tokens=400,
                                     output_tokens=100, cost_usd=0.02))
        db_session.commit()

        body = client.get("/analytics/llm-usage").json()

        assert body["provider"] == "openai"
        assert body["model"] == MODEL
        assert body["today"]["calls"] == 2
        assert body["today"]["cost_usd"] == pytest.approx(0.02)
        assert body["today"]["remaining_usd"] == pytest.approx(0.13)
        assert body["today"]["exhausted"] is False
        assert body["month_to_date"]["remaining_usd"] == pytest.approx(0.98)

    def test_empty_ledger_reports_zeroes_not_an_error(self, client, clean_ledger):
        body = client.get("/analytics/llm-usage").json()
        assert body["today"]["calls"] == 0
        assert body["today"]["cost_usd"] == 0.0

    def test_never_leaks_the_key(self, client, monkeypatch, live_openai, clean_ledger):
        raw = client.get("/analytics/llm-usage").text
        assert settings.openai_api_key not in raw
        assert "api_key" not in raw


class TestApiSurface:
    def test_misconfiguration_returns_503_not_500(self, client, monkeypatch, sample_profile):
        """An operator error should be a clear 503, never a 200 carrying a
        mock verdict the caller can't distinguish from a real one."""
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "")

        response = client.post("/transactions/simulate", json={
            "user_id": sample_profile.user_id,
            "amount": 150.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 1000.0,
            "origin_balance_after": 850.0,
            "location_country": "US",
            "occurred_at": "2024-06-15T10:00:00",
        })

        assert response.status_code == 503
        body = response.json()
        assert "no API key" in body["detail"]
        assert "LLM_PROVIDER=mock" in body["hint"]
