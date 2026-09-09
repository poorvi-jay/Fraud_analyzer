"""Tests for the LLM-backed context agent path.

The PRD's acceptance criterion for the context agent is "given the same test
cases used during scaffolding, returns structured plausibility judgments".
These tests hold the *structure* end of that -- prompt contents, response
parsing, the mock fallback, and provider selection -- against a stubbed
Anthropic client, so the contract is verified on every CI run with no API
key and no network.

What they deliberately do NOT check is judgment quality on real model
output; that needs a live key and is what scripts/check_context_agent.py is
for (see its docstring).
"""

import json
from datetime import date, datetime

import pytest

from app.agents import context_agent
from app.config import settings


class _StubMessages:
    def __init__(self, text: str | None, raises: Exception | None = None):
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises

        class _Block:
            text = self._text

        class _Response:
            content = [_Block()]

        return _Response()


class _StubClient:
    def __init__(self, messages: _StubMessages):
        self.messages = messages


@pytest.fixture
def anthropic_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-not-a-real-key")
    monkeypatch.setattr(settings, "llm_model", "claude-haiku-4-5-20251001")


def _install_stub(monkeypatch, text: str | None = None, raises: Exception | None = None) -> _StubMessages:
    import anthropic

    stub_messages = _StubMessages(text, raises)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: _StubClient(stub_messages))
    return stub_messages


TXN = {
    "id": "should-not-be-in-prompt",
    "amount": 4000.0,
    "transaction_type": "PAYMENT",
    "origin_balance_before": 9000.0,
    "origin_balance_after": 5000.0,
    "location_country": "FR",
    "occurred_at": datetime(2024, 6, 15, 10, 0, 0),
}
PROFILE = {
    "user_id": "C_TEST",
    "account_created": date(2022, 1, 1),
    "home_country": "US",
    "typical_transaction_amount": 200.0,
    "travel_frequency": "never",
}


# --- provider selection ---


def test_provider_status_defaults_to_mock():
    status = context_agent.provider_status()
    assert status["active_provider"] == "mock"
    assert status["configured_provider"] == "mock"


def test_provider_status_reports_anthropic_when_key_is_set(anthropic_provider):
    status = context_agent.provider_status()
    assert status["active_provider"] == "anthropic"
    assert status["model"] == "claude-haiku-4-5-20251001"


def test_anthropic_without_a_key_falls_back_to_mock_and_says_why(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    status = context_agent.provider_status()
    assert status["active_provider"] == "mock"
    assert "ANTHROPIC_API_KEY is empty" in status["reason"]


def test_unknown_provider_falls_back_to_mock_rather_than_crashing(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gpt-9000")
    status = context_agent.provider_status()
    assert status["active_provider"] == "mock"
    assert "unknown LLM_PROVIDER" in status["reason"]


def test_run_uses_the_mock_heuristic_when_no_provider_is_configured():
    opinion = context_agent.run(TXN, PROFILE)
    assert "[mock heuristic" in opinion.reasoning


# --- prompt ---


def test_prompt_carries_the_profile_comparison_signals_and_omits_the_row_id():
    signals = {"is_foreign": True, "travel_frequency": "never", "amount_to_typical_ratio": 20.0}
    prompt = context_agent.build_prompt(TXN, PROFILE, signals)

    assert "amount_to_typical_ratio" in prompt
    assert '"plausible"' in prompt
    # The DB row id is an internal identifier with no bearing on plausibility;
    # sending it just invites the model to reason about something meaningless.
    assert "should-not-be-in-prompt" not in prompt


# --- response parsing ---


def test_parses_a_plain_json_object():
    assert context_agent.parse_response('{"plausible": true, "reasoning": "Matches profile."}') == (
        True,
        "Matches profile.",
    )


def test_parses_a_markdown_fenced_object():
    text = '```json\n{"plausible": false, "reasoning": "Foreign, 20x typical."}\n```'
    plausible, reasoning = context_agent.parse_response(text)
    assert plausible is False
    assert reasoning == "Foreign, 20x typical."


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        '{"reasoning": "missing the verdict"}',
        '{"plausible": "yes", "reasoning": "a string, not a bool"}',
        '["plausible"]',
    ],
)
def test_rejects_anything_that_is_not_a_clear_structured_judgment(text):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        context_agent.parse_response(text)


# --- end-to-end through run() ---


def test_plausible_llm_verdict_produces_a_clear_low_score_opinion(monkeypatch, anthropic_provider):
    stub = _install_stub(
        monkeypatch, '{"plausible": true, "reasoning": "Consistent with a frequent traveller."}'
    )
    opinion = context_agent.run(TXN, PROFILE)

    assert opinion.agent_name == "context_agent"
    assert opinion.flag is False
    assert opinion.score < context_agent.SUSPICION_THRESHOLD
    assert opinion.reasoning == "Consistent with a frequent traveller."
    assert stub.calls[0]["model"] == "claude-haiku-4-5-20251001"


def test_implausible_llm_verdict_flags(monkeypatch, anthropic_provider):
    _install_stub(monkeypatch, '{"plausible": false, "reasoning": "Never travels; 20x typical spend."}')
    opinion = context_agent.run(TXN, PROFILE)

    assert opinion.flag is True
    assert opinion.score > context_agent.SUSPICION_THRESHOLD


def test_api_failure_falls_back_to_the_mock_heuristic_and_labels_it(monkeypatch, anthropic_provider):
    _install_stub(monkeypatch, raises=RuntimeError("connection reset"))
    opinion = context_agent.run(TXN, PROFILE)

    # The pipeline must still produce a usable opinion -- but one that says,
    # in the text a reviewer reads, that no LLM actually judged this case.
    assert "[LLM call failed" in opinion.reasoning
    assert "connection reset" in opinion.reasoning
    assert "[mock heuristic" in opinion.reasoning
    assert 0.0 <= opinion.score <= 1.0


def test_unparseable_llm_output_falls_back_rather_than_guessing(monkeypatch, anthropic_provider):
    _install_stub(monkeypatch, "I think this one is probably fine, honestly.")
    opinion = context_agent.run(TXN, PROFILE)

    assert "[LLM call failed" in opinion.reasoning
    assert "[mock heuristic" in opinion.reasoning
