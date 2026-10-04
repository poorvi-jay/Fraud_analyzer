"""Token-usage accounting and cost estimation for LLM-backed agents.

One place for pricing, so the runtime path (context_agent) and the offline
path (ml/evaluate_baseline.py) can never disagree about what a call costs.

Prices are per 1M tokens, in USD, as published by each provider. They are
hardcoded on purpose -- a cost guard that silently no-ops when a pricing
lookup fails is worse than no guard. If a provider changes its prices,
this table is the single thing to update, and PRICING_AS_OF says how stale
it is.
"""
import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PRICING_AS_OF = "2026-09-20"

# model id -> (USD per 1M input tokens, USD per 1M output tokens)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Measured against a representative prompt built by context_agent from a
# real PaySim row (725 characters -> ~260 input tokens; the response is a
# two-field JSON object). Used ONLY for pre-flight estimates, before any
# call has been made. Actual billed usage is always read back from the
# provider response -- never estimated after the fact.
EST_INPUT_TOKENS_PER_CALL = 260
EST_OUTPUT_TOKENS_PER_CALL = 55


class UnknownModelPricingError(KeyError):
    """No published price for this model id, so cost guards can't be enforced."""


def price_for(model: str) -> tuple[float, float]:
    try:
        return MODEL_PRICING[model]
    except KeyError:
        raise UnknownModelPricingError(
            f"No pricing entry for model {model!r}. Add it to MODEL_PRICING in "
            f"backend/app/llm_usage.py (current table is as of {PRICING_AS_OF}) "
            f"-- refusing to estimate cost from a guess."
        ) from None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = price_for(model)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def estimate_cost_usd(model: str, n_calls: int) -> float:
    """Pre-flight estimate for n_calls that have not happened yet."""
    return cost_usd(
        model,
        EST_INPUT_TOKENS_PER_CALL * n_calls,
        EST_OUTPUT_TOKENS_PER_CALL * n_calls,
    )


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class UsageTracker:
    """Process-wide running total of live LLM spend.

    Thread-safe because FastAPI serves requests on a threadpool; an
    undercounted total would defeat the point of having a cap at all.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals = UsageTotals()

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        call_cost = cost_usd(model, input_tokens, output_tokens)
        with self._lock:
            self._totals.calls += 1
            self._totals.input_tokens += input_tokens
            self._totals.output_tokens += output_tokens
            self._totals.cost_usd += call_cost
            running = self._totals.cost_usd
            n = self._totals.calls
        logger.info(
            "llm_call model=%s in=%d out=%d cost_usd=%.6f "
            "session_calls=%d session_cost_usd=%.4f",
            model, input_tokens, output_tokens, call_cost, n, running,
        )
        return call_cost

    @property
    def totals(self) -> UsageTotals:
        with self._lock:
            return UsageTotals(
                calls=self._totals.calls,
                input_tokens=self._totals.input_tokens,
                output_tokens=self._totals.output_tokens,
                cost_usd=self._totals.cost_usd,
            )

    def reset(self) -> None:
        with self._lock:
            self._totals = UsageTotals()


tracker = UsageTracker()
