"""Context agent: judges whether a transaction fits *this user's* behavior.

Provider-agnostic by design (per docs/ARCHITECTURE.md). LLM_PROVIDER=mock
(the default, no API key required) uses a deterministic profile-comparison
heuristic so the pipeline is fully runnable without credentials -- its
reasoning is clearly labeled as such, so the demo never overstates what's
actually LLM-backed. LLM_PROVIDER=anthropic calls the Anthropic API with a
structured prompt; adding another provider is a new function with the same
signature (transaction, profile, signals) -> AgentOpinion.
"""
import json
import logging

from app.agents.base import AgentOpinion
from app.config import settings
from app.feature_engineering import build_context_signals

logger = logging.getLogger(__name__)

SUSPICION_THRESHOLD = 0.5

SUPPORTED_PROVIDERS = ("mock", "anthropic")


def provider_status() -> dict:
    """What the context agent will actually do, and why.

    Exposed on GET /health and logged at startup because the failure mode
    this guards against is silent: LLM_PROVIDER=anthropic with a missing or
    misspelled key falls back to the mock heuristic, and a demo that claims
    to be LLM-backed while quietly running a heuristic is exactly the
    overstatement this project's README goes out of its way to avoid.
    """
    configured = settings.llm_provider
    if configured == "anthropic" and settings.anthropic_api_key:
        return {
            "configured_provider": configured,
            "active_provider": "anthropic",
            "model": settings.llm_model,
            "reason": "ANTHROPIC_API_KEY is set",
        }
    if configured == "anthropic":
        return {
            "configured_provider": configured,
            "active_provider": "mock",
            "model": None,
            "reason": "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty -- falling back to the mock heuristic",
        }
    if configured not in SUPPORTED_PROVIDERS:
        return {
            "configured_provider": configured,
            "active_provider": "mock",
            "model": None,
            "reason": f"unknown LLM_PROVIDER={configured!r} (supported: {', '.join(SUPPORTED_PROVIDERS)})",
        }
    return {
        "configured_provider": configured,
        "active_provider": "mock",
        "model": None,
        "reason": "LLM_PROVIDER=mock -- deterministic heuristic, no API key needed",
    }


def log_provider_status() -> dict:
    """Called once at app startup (app/main.py). Warns loudly when the
    configured provider is not the one that will run.
    """
    status = provider_status()
    if status["configured_provider"] != status["active_provider"]:
        logger.warning("context_agent: %s", status["reason"])
    else:
        logger.info("context_agent: %s", status["reason"])
    return status


def run(transaction: dict, profile: dict) -> AgentOpinion:
    signals = build_context_signals(transaction, profile)
    if provider_status()["active_provider"] == "anthropic":
        return _run_anthropic(transaction, profile, signals)
    return _run_mock(signals)


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


def build_prompt(transaction: dict, profile: dict, signals: dict) -> str:
    """The context agent's whole prompt. Factored out of the API call so it
    can be asserted on in tests without a network round trip.
    """
    return f"""You are a fraud analyst judging whether ONE transaction is plausible given a user's profile.
Respond with ONLY a JSON object: {{"plausible": bool, "reasoning": "one or two sentences"}}.

Transaction: {json.dumps({k: v for k, v in transaction.items() if k != 'id'}, default=str)}
User profile: {json.dumps(profile, default=str)}
Derived comparison signals: {json.dumps(signals, default=str)}
"""


def parse_response(text: str) -> tuple[bool, str]:
    """Parse the model's reply into (plausible, reasoning).

    Tolerates a ```json fenced block around the object, which models emit
    fairly often despite the "ONLY a JSON object" instruction. Raises on
    anything else -- run() turns that into the labelled mock fallback rather
    than guessing at a judgment the model didn't clearly make.
    """
    text = text.strip()
    if text.startswith("```"):
        # Strip a leading ```json / ``` fence and the trailing fence.
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict) or "plausible" not in parsed or "reasoning" not in parsed:
        raise ValueError(f"expected an object with 'plausible' and 'reasoning', got: {text[:200]!r}")
    if not isinstance(parsed["plausible"], bool):
        raise ValueError(f"'plausible' must be a bool, got {parsed['plausible']!r}")
    return parsed["plausible"], str(parsed["reasoning"])


def _run_anthropic(transaction: dict, profile: dict, signals: dict) -> AgentOpinion:
    import anthropic

    prompt = build_prompt(transaction, profile, signals)
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.llm_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        plausible, reasoning = parse_response(response.content[0].text)
    except Exception as exc:  # LLM/parsing failure: fall back to the mock heuristic, don't crash the pipeline
        logger.warning("context_agent: Anthropic call failed (%s); using mock heuristic", exc)
        opinion = _run_mock(signals)
        return AgentOpinion(
            agent_name="context_agent",
            score=opinion.score,
            flag=opinion.flag,
            reasoning=f"[LLM call failed ({exc}), fell back to mock heuristic] {opinion.reasoning}",
        )

    score = 0.15 if plausible else 0.85
    return AgentOpinion(agent_name="context_agent", score=score, flag=not plausible, reasoning=reasoning)
