"""Live acceptance check for the LLM-backed context agent.

PRD 7.1's acceptance criterion for the context agent is: "given the same
test cases used during scaffolding, returns structured plausibility
judgments". tests/test_context_agent_llm.py holds that contract against a
stubbed client on every CI run; this script is the other half -- it runs the
same cases against a *real* API key, which CI cannot do.

    cd backend
    LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... python scripts/check_context_agent.py

It asserts structure, not verdicts: a fraud judgment is not a fixed-answer
problem, and pinning the model to one expected label per case would be a
test of this script's opinions rather than the agent's contract. What it
enforces is that every case came back as a real parsed LLM judgment -- no
silent fallback to the mock heuristic -- and it prints each judgment so a
human can read whether the reasoning is sane. Exit code 1 if any case fell
back.

The "expected" column is a human sanity anchor, printed and compared but
never fatal on its own: a divergence is flagged for you to read, not
treated as a failed build.
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import context_agent  # noqa: E402
from app.config import settings  # noqa: E402

OCCURRED_AT = datetime(2024, 6, 15, 14, 30, 0)

# The scaffolding cases: each isolates one axis the context agent exists to
# judge -- location vs. travel habits, and amount vs. this user's norm.
CASES = [
    {
        "name": "home-country purchase, typical size",
        "expect_plausible": True,
        "transaction": {
            "amount": 180.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 4200.0,
            "origin_balance_after": 4020.0,
            "location_country": "US",
            "occurred_at": OCCURRED_AT,
        },
        "profile": {
            "user_id": "C_HOMEBODY",
            "account_created": date(2021, 3, 1),
            "home_country": "US",
            "typical_transaction_amount": 200.0,
            "travel_frequency": "never",
        },
    },
    {
        "name": "foreign, 25x typical, user never travels",
        "expect_plausible": False,
        "transaction": {
            "amount": 5000.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 6000.0,
            "origin_balance_after": 1000.0,
            "location_country": "RO",
            "occurred_at": OCCURRED_AT,
        },
        "profile": {
            "user_id": "C_HOMEBODY",
            "account_created": date(2021, 3, 1),
            "home_country": "US",
            "typical_transaction_amount": 200.0,
            "travel_frequency": "never",
        },
    },
    {
        "name": "foreign, large, but a frequent traveller",
        "expect_plausible": True,
        "transaction": {
            "amount": 3000.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 40000.0,
            "origin_balance_after": 37000.0,
            "location_country": "JP",
            "occurred_at": OCCURRED_AT,
        },
        "profile": {
            "user_id": "C_TRAVELLER",
            "account_created": date(2019, 8, 1),
            "home_country": "US",
            "typical_transaction_amount": 1800.0,
            "travel_frequency": "frequent",
        },
    },
    {
        "name": "home country, but 40x this user's typical amount",
        "expect_plausible": False,
        "transaction": {
            "amount": 8000.0,
            "transaction_type": "TRANSFER",
            "origin_balance_before": 8200.0,
            "origin_balance_after": 200.0,
            "location_country": "US",
            "occurred_at": OCCURRED_AT,
        },
        "profile": {
            "user_id": "C_SMALLSPEND",
            "account_created": date(2023, 11, 1),
            "home_country": "US",
            "typical_transaction_amount": 200.0,
            "travel_frequency": "rare",
        },
    },
]


def main() -> int:
    status = context_agent.provider_status()
    print(f"configured provider : {status['configured_provider']}")
    print(f"active provider     : {status['active_provider']}")
    print(f"model               : {status['model']}")
    print(f"reason              : {status['reason']}\n")

    if status["active_provider"] != "anthropic":
        print(
            "This script only means anything against a live provider.\n"
            "Re-run with: LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... "
            "python scripts/check_context_agent.py"
        )
        return 1

    fell_back = 0
    diverged = 0
    for case in CASES:
        opinion = context_agent.run(case["transaction"], case["profile"])
        is_fallback = "[mock heuristic" in opinion.reasoning or "[LLM call failed" in opinion.reasoning
        judged_plausible = not opinion.flag

        print(f"--- {case['name']}")
        print(f"    score     : {opinion.score:.2f}  ({'flagged' if opinion.flag else 'clear'})")
        print(f"    expected  : {'plausible' if case['expect_plausible'] else 'implausible'}")
        print(f"    reasoning : {opinion.reasoning}")

        if is_fallback:
            fell_back += 1
            print("    RESULT    : FAILED -- no live LLM judgment (fell back to the heuristic)")
        elif judged_plausible != case["expect_plausible"]:
            diverged += 1
            print("    RESULT    : ok (structured judgment), but diverges from the sanity anchor -- read it")
        else:
            print("    RESULT    : ok")
        print()

    print(f"{len(CASES)} cases | model={settings.llm_model} | fallbacks={fell_back} | divergences={diverged}")
    if fell_back:
        print("FAIL: at least one case did not produce a live, parseable LLM judgment.")
        return 1
    print("PASS: every case returned a structured plausibility judgment from the live provider.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
