"""
Make exactly ONE live LLM call and report what actually happened.

Why this exists: every test in backend/tests/test_llm_budget.py monkeypatches
the provider call, so they prove our logic but never exercise the real SDK.
Two details in _call_openai were written from general knowledge rather than a
verified reference -- the `max_completion_tokens` parameter name and the
`gpt-5.6-luna` model string -- and a wrong guess on either is a 400 that no
unit test can catch.

This script closes that gap for about $0.0001. It checks four things a mocked
test cannot:

  1. the SDK call shape is accepted by the provider
  2. a real response parses into an AgentOpinion
  3. billed token counts resemble the 260/55 estimate the cost guards use
  4. the spend lands in the llm_daily_spend ledger

ONE call. No loops, no retries, no batch. If it fails, it fails once.

    python scripts/smoke_llm.py          # asks for confirmation
    python scripts/smoke_llm.py --yes    # skips it

Runs against whatever DATABASE_URL the repo-root .env points at, which is
where the ledger row will appear. The budget caps apply as normal.
"""
import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import context_agent  # noqa: E402
from app.agents.context_agent import LLMConfigurationError  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.llm_budget import flush, remaining  # noqa: E402
from app.llm_usage import (  # noqa: E402
    EST_INPUT_TOKENS_PER_CALL,
    EST_OUTPUT_TOKENS_PER_CALL,
    estimate_cost_usd,
    tracker,
)

# A deliberately unremarkable transaction: in-country, close to the profile's
# typical amount. The point is to exercise the call, not to probe the model's
# judgement, so either verdict is a pass.
PROFILE = {
    "user_id": "C_SMOKE_TEST",
    "account_created": date(2020, 1, 1),
    "home_country": "US",
    "typical_transaction_amount": 200.0,
    "travel_frequency": "never",
}
TRANSACTION = {
    "amount": 180.0,
    "transaction_type": "PAYMENT",
    "origin_balance_before": 1000.0,
    "origin_balance_after": 820.0,
    "location_country": "US",
    "occurred_at": datetime(2024, 6, 15, 10, 0, 0),
}


def _fail(message: str, *hints: str) -> None:
    print(f"\nFAILED: {message}", file=sys.stderr)
    for hint in hints:
        print(f"  - {hint}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()

    if settings.llm_provider == "mock":
        _fail(
            "LLM_PROVIDER=mock, so there is nothing live to smoke-test.",
            "Set LLM_PROVIDER=openai in the repo-root .env and re-run.",
        )

    init_db()  # ensure llm_daily_spend exists before we read or write it
    left_today, left_month = remaining()
    est = estimate_cost_usd(settings.llm_model, 1)

    print(f"provider     : {settings.llm_provider}")
    print(f"model        : {settings.llm_model}")
    print(f"database     : {settings.database_url.split(':')[0]}  (where the ledger row lands)")
    print(f"est. cost    : ~${est:.6f} for one call")
    print(f"budget left  : ${left_today:.4f} today, ${left_month:.4f} this month")

    if not args.yes and input("\nMake one live call? Type 'yes': ").strip().lower() != "yes":
        print("Aborted. Nothing was called, nothing was billed.")
        sys.exit(1)

    print("\nCalling...", flush=True)
    try:
        opinion = context_agent.run(TRANSACTION, PROFILE)
    except LLMConfigurationError as exc:
        _fail(
            f"misconfiguration -- {exc}",
            "A 404 usually means the model string is wrong for this API.",
            "A 401 means the key is wrong, empty, or not loaded from the .env you edited.",
            "Remember there are TWO .env files: the repo root one is what this script reads.",
        )

    flush()  # let the background ledger write land before we read it back
    usage = tracker.totals

    # A fallback still returns an opinion, so the labelled reasoning is the
    # only way to tell a real answer from a degraded one. Check it explicitly
    # rather than treating "no exception" as success.
    if usage.calls == 0:
        print(f"\nAgent reasoning: {opinion.reasoning}")
        _fail(
            "no live call was made -- the agent fell back to the mock.",
            "If the reasoning says 'budget reached', raise LLM_DAILY_BUDGET_USD or wait for the UTC reset.",
            "If it says the call failed, the message in brackets is the provider's own error.",
            "Either way the SDK call shape is still unverified.",
        )

    print("\n--- the call worked ---")
    print(f"verdict      : {'plausible' if not opinion.flag else 'implausible'} (score {opinion.score})")
    print(f"reasoning    : {opinion.reasoning}")

    print("\n--- token usage: billed vs. the estimate the cost guards use ---")
    for label, actual, assumed in (
        ("input", usage.input_tokens, EST_INPUT_TOKENS_PER_CALL),
        ("output", usage.output_tokens, EST_OUTPUT_TOKENS_PER_CALL),
    ):
        drift = (actual - assumed) / assumed * 100
        print(f"{label:<7}: {actual:>5} billed vs {assumed:>5} assumed  ({drift:+.0f}%)")
    print(f"cost   : ${usage.cost_usd:.6f} actual vs ${est:.6f} estimated")

    if max(abs(usage.input_tokens - EST_INPUT_TOKENS_PER_CALL) / EST_INPUT_TOKENS_PER_CALL,
           abs(usage.output_tokens - EST_OUTPUT_TOKENS_PER_CALL) / EST_OUTPUT_TOKENS_PER_CALL) > 0.30:
        print("\nNOTE: billed tokens are more than 30% off the estimate. The caps still hold"
              "\n(they are enforced on billed usage, not the estimate), but the pre-flight"
              "\nfigures in evaluate_baseline/seed will be misleading. Consider updating"
              "\nEST_INPUT_TOKENS_PER_CALL / EST_OUTPUT_TOKENS_PER_CALL in backend/app/llm_usage.py.")

    print("\n--- ledger ---")
    from app.db import SessionLocal
    from app.models import LLMDailySpend

    today = datetime.now(timezone.utc).date()  # the ledger keys on UTC, not local
    with SessionLocal() as db:
        row = db.get(LLMDailySpend, today)
    if row is None:
        _fail(
            f"no llm_daily_spend row for {today} (UTC) -- the call was billed but not recorded.",
            "Spend that isn't recorded isn't capped. Check the logs for 'write FAILED'.",
        )
    print(f"{today} (UTC): {row.calls} call(s), {row.input_tokens} in / {row.output_tokens} out, ${row.cost_usd:.6f}")

    print("\nAll four checks passed: call shape, response parsing, token accounting, ledger write.")


if __name__ == "__main__":
    main()
