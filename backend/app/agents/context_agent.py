"""Context agent: judges whether a transaction fits *this user's* behavior.

Provider-agnostic by design (per docs/ARCHITECTURE.md). LLM_PROVIDER=mock
(no API key required) uses a deterministic profile-comparison heuristic so
the pipeline is fully runnable without credentials -- its reasoning is
clearly labeled as such, so the demo never overstates what's actually
LLM-backed. LLM_PROVIDER=openai or =anthropic calls that provider with the
same structured prompt; adding another is one function with the same
signature (transaction, profile, signals) -> AgentOpinion.

Failure policy -- two kinds of failure, deliberately handled differently:

  * Misconfiguration (no key, bad key, unknown model, unknown provider)
    raises LLMConfigurationError and does NOT fall back. These are wrong
    on every call, forever, and a silent fallback would mean running the
    mock heuristic while believing the demo is LLM-backed -- visible only
    as a line of prefix text buried in each case's reasoning.
  * Transient failure (timeout, rate limit, 5xx, unparseable response)
    DOES fall back to the mock, labeled inline. One bad call shouldn't
    take down a scoring request.
  * Budget reached (daily or monthly cap in llm_budget) also falls back,
    labeled "[LLM budget reached ...]". This is a deliberate, visible
    degradation -- the key is fine, we've just chosen to stop spending --
    so the public demo keeps answering rather than 503ing for the rest of
    the month.
"""
import json
import logging

from app.agents.base import AgentOpinion
from app.config import settings
from app.feature_engineering import build_context_signals
from app.llm_budget import LLMBudgetExceeded, check_budget, record_spend
from app.llm_usage import UnknownModelPricingError, price_for, tracker

logger = logging.getLogger(__name__)

SUSPICION_THRESHOLD = 0.5
MAX_OUTPUT_TOKENS = 300

PROMPT_TEMPLATE = """You are a fraud analyst judging whether ONE transaction is plausible given a user's profile.
Respond with ONLY a JSON object: {{"plausible": bool, "reasoning": "one or two sentences"}}.

Transaction: {transaction}
User profile: {profile}
Derived comparison signals: {signals}
"""


class LLMConfigurationError(RuntimeError):
    """The LLM provider is misconfigured. Not recoverable by retrying."""


def run(transaction: dict, profile: dict) -> AgentOpinion:
    signals = build_context_signals(transaction, profile)
    provider = settings.llm_provider

    if provider == "mock":
        return _run_mock(signals)
    if provider == "openai":
        return _run_llm(_call_openai, transaction, profile, signals)
    if provider == "anthropic":
        return _run_llm(_call_anthropic, transaction, profile, signals)

    raise LLMConfigurationError(
        f"LLM_PROVIDER={provider!r} is not a known provider "
        f"(expected 'mock', 'openai' or 'anthropic')."
    )


def _run_mock(signals: dict) -> AgentOpinion:
    """Deterministic heuristic standing in for an LLM call: is this
    transaction's location and size consistent with the user's known
    travel habits and typical spend?
    """
    suspicion = 0.1
    frequency = signals["travel_frequency"]
    ratio = signals["amount_to_typical_ratio"]

    if signals["is_foreign"]:
        suspicion += {"never": 0.6, "rare": 0.3, "frequent": 0.05}[frequency]
    if ratio > 5:
        suspicion += 0.05 if frequency == "frequent" else 0.25
    elif ratio > 2:
        suspicion += 0.05

    suspicion = min(suspicion, 1.0)
    flag = suspicion >= SUSPICION_THRESHOLD

    verdict = "does not fit" if flag else "is consistent with"
    reasoning = (
        f"[mock heuristic -- no LLM configured] Transaction {verdict} this user's profile: "
        f"{'foreign' if signals['is_foreign'] else 'home-country'} location, "
        f"{frequency} travel history, {ratio:.1f}x their typical amount."
    )
    return AgentOpinion(agent_name="context_agent", score=round(suspicion, 3), flag=flag, reasoning=reasoning)


def _build_prompt(transaction: dict, profile: dict, signals: dict) -> str:
    return PROMPT_TEMPLATE.format(
        transaction=json.dumps({k: v for k, v in transaction.items() if k != "id"}, default=str),
        profile=json.dumps(profile, default=str),
        signals=json.dumps(signals, default=str),
    )


def _require_key(provider: str, key: str) -> None:
    if not key:
        raise LLMConfigurationError(
            f"LLM_PROVIDER={provider!r} but no API key is set. Set "
            f"{provider.upper()}_API_KEY in the backend environment, or set "
            f"LLM_PROVIDER=mock to run the deterministic heuristic instead."
        )


def _check_model_priceable() -> None:
    """Refuse to spend money on a model we can't price -- an unpriceable
    model means the spend caps in llm_budget can't be enforced either.

    Re-raised as LLMConfigurationError so it surfaces as a clear 503 like
    every other misconfiguration, rather than a bare KeyError and a 500.
    """
    try:
        price_for(settings.llm_model)
    except UnknownModelPricingError as exc:
        raise LLMConfigurationError(
            f"LLM_MODEL={settings.llm_model!r} has no pricing entry, so the spend caps "
            f"cannot be enforced. {exc} Note that a duplicated LLM_MODEL line in .env "
            f"silently resolves to the LAST one."
        ) from exc


def _call_openai(prompt: str) -> tuple[str, int, int]:
    """-> (response text, input tokens, output tokens). Raises on failure."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.llm_model,
        max_completion_tokens=MAX_OUTPUT_TOKENS,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    return (
        response.choices[0].message.content.strip(),
        usage.prompt_tokens,
        usage.completion_tokens,
    )


def _call_anthropic(prompt: str) -> tuple[str, int, int]:
    """-> (response text, input tokens, output tokens). Raises on failure."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return (
        response.content[0].text.strip(),
        response.usage.input_tokens,
        response.usage.output_tokens,
    )


def _is_configuration_error(exc: Exception) -> bool:
    """Authentication, permission, unknown-model and bad-request failures are
    configuration problems: they will fail identically on every subsequent
    call, so falling back to the mock would hide them indefinitely.

    Matched structurally (HTTP status) rather than by provider exception
    class, so this stays correct for both SDKs and for a third provider
    added later. 401 invalid key, 403 no access, 404 unknown model.
    """
    status = getattr(exc, "status_code", None)
    if status in (401, 403, 404):
        return True
    # A 400 naming the model is an unknown/retired model id, not a transient
    # fault. Other 400s (e.g. an over-long prompt) are treated as transient.
    if status == 400 and "model" in str(exc).lower():
        return True
    return False


def _run_llm(call, transaction: dict, profile: dict, signals: dict) -> AgentOpinion:
    provider = settings.llm_provider
    key = settings.openai_api_key if provider == "openai" else settings.anthropic_api_key
    _require_key(provider, key)
    _check_model_priceable()

    try:
        check_budget(settings.llm_model)
    except LLMBudgetExceeded as exc:
        logger.warning("context_agent skipping %s call: %s", provider, exc)
        return _fallback(signals, f"LLM budget reached: {exc}")

    prompt = _build_prompt(transaction, profile, signals)

    try:
        text, input_tokens, output_tokens = call(prompt)
    except LLMConfigurationError:
        raise
    except Exception as exc:
        if _is_configuration_error(exc):
            raise LLMConfigurationError(
                f"{provider} rejected the request as misconfigured "
                f"(model={settings.llm_model!r}): {exc}. Not falling back to the "
                f"mock heuristic -- fix the key or model id, or set LLM_PROVIDER=mock."
            ) from exc
        logger.warning("context_agent %s call failed transiently: %s", provider, exc)
        return _fallback(signals, f"LLM call failed ({exc})")

    # Record before parsing: the call was billed whether or not its output
    # turns out to be usable.
    call_cost = tracker.record(settings.llm_model, input_tokens, output_tokens)
    record_spend(input_tokens, output_tokens, call_cost)

    try:
        parsed = json.loads(text)
        plausible = bool(parsed["plausible"])
        reasoning = str(parsed["reasoning"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("context_agent %s returned unparseable output: %s", provider, exc)
        return _fallback(signals, f"LLM returned unparseable output ({exc})")

    score = 0.15 if plausible else 0.85
    return AgentOpinion(agent_name="context_agent", score=score, flag=not plausible, reasoning=reasoning)


def _fallback(signals: dict, why: str) -> AgentOpinion:
    opinion = _run_mock(signals)
    return AgentOpinion(
        agent_name="context_agent",
        score=opinion.score,
        flag=opinion.flag,
        reasoning=f"[{why}, fell back to mock heuristic] {opinion.reasoning}",
    )
