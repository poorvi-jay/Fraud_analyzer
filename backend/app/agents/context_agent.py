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

from app.agents.base import AgentOpinion
from app.config import settings
from app.feature_engineering import build_context_signals

SUSPICION_THRESHOLD = 0.5


def run(transaction: dict, profile: dict) -> AgentOpinion:
    signals = build_context_signals(transaction, profile)
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
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


def _run_anthropic(transaction: dict, profile: dict, signals: dict) -> AgentOpinion:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = f"""You are a fraud analyst judging whether ONE transaction is plausible given a user's profile.
Respond with ONLY a JSON object: {{"plausible": bool, "reasoning": "one or two sentences"}}.

Transaction: {json.dumps({k: v for k, v in transaction.items() if k != 'id'}, default=str)}
User profile: {json.dumps(profile, default=str)}
Derived comparison signals: {json.dumps(signals, default=str)}
"""
    try:
        response = client.messages.create(
            model=settings.llm_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        parsed = json.loads(text)
        plausible = bool(parsed["plausible"])
        reasoning = str(parsed["reasoning"])
    except Exception as exc:  # LLM/parsing failure: fall back to the mock heuristic, don't crash the pipeline
        opinion = _run_mock(signals)
        return AgentOpinion(
            agent_name="context_agent",
            score=opinion.score,
            flag=opinion.flag,
            reasoning=f"[LLM call failed ({exc}), fell back to mock heuristic] {opinion.reasoning}",
        )

    score = 0.15 if plausible else 0.85
    return AgentOpinion(agent_name="context_agent", score=score, flag=not plausible, reasoning=reasoning)
