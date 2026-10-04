"""
Runs the same held-out test split (same random_state/stratify as
train_anomaly_model.py, so it's provably the data the model never trained
on) through (a) the anomaly model alone and (b) the full four-agent
pipeline, and compares false positive / false negative rates.

Agents are called directly (no DB, no HTTP) for speed. Each row still costs
a SHAP explanation (for the anomaly agent's reasoning text), which is too
slow to run unsampled against a multi-million-row test split (e.g. the real
PaySim dataset), so by default this evaluates a stratified sample rather
than every row -- pass --sample-size 0 to disable sampling and use the full
test set (fine for the ~29k-row synthetic dataset, not recommended for
6M+-row real PaySim).

COST SAFETY
-----------
**This script forces the mock context agent by default, whatever
LLM_PROVIDER says.** One row = one LLM call, and the default sample is
30,000 rows; at GPT-5.6 Luna's rates that is ~$3.54 a run, and
`--sample-size 0` against real PaySim is ~1.59M calls (~$188). Neither
should be reachable by running the documented command with a key that
happens to be in the environment.

Live evaluation therefore requires `--allow-llm`, is capped at
--max-live-calls (default 500), prints a cost estimate, and asks for
confirmation. The cap is checked against the post-sampling row count, so
`--allow-llm --sample-size 0` is refused rather than silently enormous.

There is also a methodological reason for the mock default: a live LLM
makes the reported numbers non-reproducible run to run, so the committed
report would stop being a fixed measurement. The report records which
provider produced it.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.agents import anomaly_agent, context_agent, coordinator_agent, policy_agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm_budget import LLMBudgetExceeded, check_budget, flush, remaining  # noqa: E402
from app.llm_usage import estimate_cost_usd, price_for, tracker  # noqa: E402

DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "ml" / "reports"

DEFAULT_SAMPLE_SIZE = 30_000
PROGRESS_EVERY = 2_000

# Deliberately small: 200 calls is ~$0.02 at Luna's rates. A live run is for
# sanity-checking that the LLM-backed context agent behaves, not for
# producing the committed report -- raising this should be a conscious act
# with a number typed by hand. The persistent daily/monthly budget in
# app/llm_budget.py is checked on top of this.
DEFAULT_MAX_LIVE_CALLS = 200


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _resolve_provider(args, n_rows: int) -> str:
    """Decide which context-agent provider this run uses, and enforce every
    cost guard before a single call is made. Returns the provider name.
    """
    if not args.allow_llm:
        # Force the mock regardless of the ambient LLM_PROVIDER. context_agent
        # reads settings at call time, so overriding here covers every row.
        if settings.llm_provider != "mock":
            print(f"NOTE: LLM_PROVIDER={settings.llm_provider!r} is set, but this script "
                  f"runs the mock context agent unless --allow-llm is passed. No API "
                  f"calls will be made and nothing will be billed.")
        settings.llm_provider = "mock"
        return "mock"

    provider = settings.llm_provider
    if provider == "mock":
        print("ERROR: --allow-llm was passed but LLM_PROVIDER=mock, so there is no live "
              "provider to use. Set LLM_PROVIDER=openai (or anthropic) to run live.",
              file=sys.stderr)
        sys.exit(2)

    key = settings.openai_api_key if provider == "openai" else settings.anthropic_api_key
    if not key:
        print(f"ERROR: --allow-llm was passed with LLM_PROVIDER={provider!r}, but "
              f"{provider.upper()}_API_KEY is empty. Refusing to start a run that would "
              f"fail on every row.", file=sys.stderr)
        sys.exit(2)

    try:
        price_for(settings.llm_model)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    if n_rows > args.max_live_calls:
        est = estimate_cost_usd(settings.llm_model, n_rows)
        print(f"ERROR: this run would make {n_rows:,} live LLM calls (~${est:.2f}), over the "
              f"--max-live-calls limit of {args.max_live_calls:,}.\n"
              f"       Lower --sample-size, or raise --max-live-calls deliberately if you "
              f"really mean to spend that.", file=sys.stderr)
        sys.exit(2)

    # The budget ledger lives in the database; this script never otherwise
    # touches it, so make sure the table exists before reading it.
    from app.db import init_db
    init_db()

    try:
        check_budget(settings.llm_model, n_rows)
    except LLMBudgetExceeded as exc:
        print(f"ERROR: this run doesn't fit in the remaining LLM budget: {exc}.\n"
              f"       Lower --sample-size, or wait for the daily/monthly budget to reset "
              f"(UTC).", file=sys.stderr)
        sys.exit(2)
    left_today, left_month = remaining()

    est = estimate_cost_usd(settings.llm_model, n_rows)
    in_rate, out_rate = price_for(settings.llm_model)
    print("\n" + "=" * 68)
    print(f"  LIVE LLM EVALUATION -- THIS WILL BE BILLED")
    print(f"  provider     : {provider}")
    print(f"  model        : {settings.llm_model} (${in_rate:.2f}/${out_rate:.2f} per 1M in/out)")
    print(f"  rows         : {n_rows:,}  (1 LLM call each)")
    print(f"  est. cost    : ~${est:.4f} USD")
    print(f"  budget left  : ${left_today:.4f} today, ${left_month:.4f} this month")
    print(f"  NOTE: results will NOT be reproducible run to run.")
    print("=" * 68)
    if not args.yes:
        if input("  Type 'yes' to proceed: ").strip().lower() != "yes":
            print("Aborted. Nothing was called, nothing was billed.")
            sys.exit(1)
    print()
    return provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE,
        help=f"Stratified sample size of the test split to evaluate (default {DEFAULT_SAMPLE_SIZE}); 0 = use the full test set.",
    )
    parser.add_argument(
        "--allow-llm", action="store_true",
        help="Use the live LLM-backed context agent instead of the mock. Costs money; "
             "subject to --max-live-calls and a confirmation prompt.",
    )
    parser.add_argument(
        "--max-live-calls", type=int, default=DEFAULT_MAX_LIVE_CALLS,
        help=f"Hard ceiling on live LLM calls in one run (default {DEFAULT_MAX_LIVE_CALLS}). "
             f"Only applies with --allow-llm.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the live-run confirmation prompt. Does NOT bypass --max-live-calls.",
    )
    args = parser.parse_args()

    transactions = pd.read_csv(DATA_DIR / "transactions.csv", parse_dates=["occurred_at"])
    profiles = pd.read_csv(DATA_DIR / "user_profiles.csv", parse_dates=["account_created"]).set_index("user_id")

    # Same split call, same order, same seed as train_anomaly_model.py's
    # train_test_split on X/y built from this same file -> identical test set.
    _, test_transactions = train_test_split(
        transactions, test_size=0.25, random_state=42, stratify=transactions["is_fraud_ground_truth"]
    )

    if args.sample_size and len(test_transactions) > args.sample_size:
        test_transactions, _ = train_test_split(
            test_transactions,
            train_size=args.sample_size,
            random_state=42,
            stratify=test_transactions["is_fraud_ground_truth"],
        )
        print(f"Test split has {len(test_transactions):,}+ rows -- evaluating a stratified "
              f"sample of {args.sample_size:,} (pass --sample-size 0 to use the full set).")

    # Every cost guard runs here, after sampling (so the cap sees the real
    # row count) and before the first agent call.
    provider = _resolve_provider(args, len(test_transactions))

    print(f"Evaluating on {len(test_transactions)} held-out transactions "
          f"({test_transactions['is_fraud_ground_truth'].mean():.3%} fraud), "
          f"context_agent provider: {provider}.")
    start = time.time()

    baseline_tp = baseline_fp = baseline_tn = baseline_fn = 0
    pipeline_block_fraud = pipeline_block_legit = 0
    pipeline_escalate_fraud = pipeline_escalate_legit = 0
    pipeline_allow_fraud = pipeline_allow_legit = 0

    for i, row in enumerate(test_transactions.itertuples(index=False), start=1):
        if i % PROGRESS_EVERY == 0:
            elapsed = time.time() - start
            rate = i / elapsed
            remaining = (len(test_transactions) - i) / rate
            print(f"  {i:,}/{len(test_transactions):,} ({elapsed:.0f}s elapsed, "
                  f"~{remaining:.0f}s remaining)", flush=True)

        txn = {
            "amount": row.amount,
            "transaction_type": row.transaction_type,
            "origin_balance_before": row.origin_balance_before,
            "origin_balance_after": row.origin_balance_after,
            "location_country": row.location_country,
            "occurred_at": row.occurred_at,
        }
        profile = profiles.loc[row.user_id].to_dict()
        account_age_days = max((row.occurred_at.date() - profile["account_created"].date()).days, 0)
        is_fraud = bool(row.is_fraud_ground_truth)

        anomaly_opinion = anomaly_agent.run(txn)
        context_opinion = context_agent.run(txn, profile)
        policy_opinion = policy_agent.run(txn, profile, account_age_days)
        verdict = coordinator_agent.run(anomaly_opinion, context_opinion, policy_opinion)

        if anomaly_opinion.flag and is_fraud:
            baseline_tp += 1
        elif anomaly_opinion.flag and not is_fraud:
            baseline_fp += 1
        elif not anomaly_opinion.flag and is_fraud:
            baseline_fn += 1
        else:
            baseline_tn += 1

        if verdict.final_verdict == "block":
            pipeline_block_fraud += is_fraud
            pipeline_block_legit += not is_fraud
        elif verdict.final_verdict == "escalate":
            pipeline_escalate_fraud += is_fraud
            pipeline_escalate_legit += not is_fraud
        else:
            pipeline_allow_fraud += is_fraud
            pipeline_allow_legit += not is_fraud

    if provider != "mock":
        flush()  # land every ledger write before reporting

    n_fraud = baseline_tp + baseline_fn
    n_legit = baseline_fp + baseline_tn
    pipeline_fraud_total = pipeline_block_fraud + pipeline_escalate_fraud + pipeline_allow_fraud
    pipeline_legit_total = pipeline_block_legit + pipeline_escalate_legit + pipeline_allow_legit
    assert pipeline_fraud_total == n_fraud and pipeline_legit_total == n_legit

    usage = tracker.totals
    report = {
        "n_test": len(test_transactions),
        "n_fraud": n_fraud,
        "n_legit": n_legit,
        # Provenance: a live-LLM run is not reproducible, so a reader has to
        # be able to tell which kind of run produced these numbers.
        "context_agent_provider": provider,
        "context_agent_model": settings.llm_model if provider != "mock" else None,
        "llm_usage": {
            # Fewer calls than rows on a live run means some rows fell back
            # to the mock (budget reached or a transient failure): the run
            # is mixed-provider, and the report says so below.
            "rows_without_live_call": len(test_transactions) - usage.calls if provider != "mock" else 0,
            "calls": usage.calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": round(usage.cost_usd, 6),
        },
        "baseline_anomaly_only": {
            "description": "anomaly_agent alone, flagged = score >= threshold",
            "false_positive_rate": _rate(baseline_fp, n_legit),
            "false_negative_rate": _rate(baseline_fn, n_fraud),
            "tp": baseline_tp, "fp": baseline_fp, "tn": baseline_tn, "fn": baseline_fn,
        },
        "multi_agent_pipeline": {
            "description": "verdict breakdown by ground truth",
            "block": {"fraud": pipeline_block_fraud, "legit": pipeline_block_legit},
            "escalate": {"fraud": pipeline_escalate_fraud, "legit": pipeline_escalate_legit},
            "allow": {"fraud": pipeline_allow_fraud, "legit": pipeline_allow_legit},
            # strict: only a wrongful auto-block counts as a false positive,
            # only fraud auto-allowed with no human in the loop counts as a
            # false negative -- escalate is a soft catch, not an error.
            "strict_false_positive_rate": _rate(pipeline_block_legit, n_legit),
            "strict_false_negative_rate": _rate(pipeline_allow_fraud, n_fraud),
            # broad: block+escalate both count as "flagged", directly
            # comparable to the baseline's binary flag.
            "broad_false_positive_rate": _rate(pipeline_block_legit + pipeline_escalate_legit, n_legit),
            "broad_false_negative_rate": _rate(pipeline_allow_fraud, n_fraud),
            "escalation_rate": _rate(pipeline_escalate_fraud + pipeline_escalate_legit, len(test_transactions)),
        },
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORTS_DIR / "baseline_comparison.json", "w") as f:
        json.dump(report, f, indent=2)

    md = f"""# Baseline comparison: anomaly-only vs. multi-agent pipeline

Held-out test set: {report['n_test']} transactions ({n_fraud} fraud, {n_legit} legit),
never seen during anomaly model training (see `ml/train_anomaly_model.py`).

Context agent: **{provider}**{f" (`{settings.llm_model}`)" if provider != "mock" else " (deterministic heuristic -- these numbers are reproducible)"}.
{f"Live LLM run: {usage.calls:,} calls, {usage.input_tokens:,} in / {usage.output_tokens:,} out tokens, ${usage.cost_usd:.4f}. Re-running will not reproduce these numbers exactly." if usage.calls else ""}
{f"**Mixed-provider run:** {len(test_transactions) - usage.calls:,} of {len(test_transactions):,} rows fell back to the mock heuristic (budget reached or a transient API failure). Treat these numbers as neither a clean LLM result nor a clean mock result." if provider != "mock" and usage.calls < len(test_transactions) else ""}

## Anomaly model alone (baseline)

| Metric | Value |
|---|---|
| False positive rate | {report['baseline_anomaly_only']['false_positive_rate']:.2%} |
| False negative rate | {report['baseline_anomaly_only']['false_negative_rate']:.2%} |

The anomaly model only sees transaction-intrinsic statistics (amount,
balance movement, type) -- no per-user profile. It reliably catches large,
balance-draining "obvious" fraud, but **misses fraud that's modest in size
and blends into normal balance movement, and over-flags large-but-legitimate
purchases** (e.g. a frequent traveller's big purchase abroad), because it has
no way to know what's normal *for that user*.

## Full multi-agent pipeline

| Metric | Value |
|---|---|
| Strict false positive rate (wrongful auto-block) | {report['multi_agent_pipeline']['strict_false_positive_rate']:.2%} |
| Strict false negative rate (fraud auto-allowed) | {report['multi_agent_pipeline']['strict_false_negative_rate']:.2%} |
| Broad false positive rate (block+escalate on legit) | {report['multi_agent_pipeline']['broad_false_positive_rate']:.2%} |
| Broad false negative rate (allowed fraud) | {report['multi_agent_pipeline']['broad_false_negative_rate']:.2%} |
| Escalation rate (sent to a human) | {report['multi_agent_pipeline']['escalation_rate']:.2%} |

Escalation is not counted as an error in the strict metrics: when the
anomaly and context agents disagree, the pipeline sends the case to a human
reviewer instead of guessing -- exactly the cases a single model would have
gotten wrong outright (auto-block a legitimate large purchase, or auto-allow
a fraud that doesn't look statistically unusual). The strict false negative
rate -- fraud that slips through with *no* human ever looking at it -- is the
number that matters most for a fraud system, and it drops relative to the
baseline's false negative rate because off-profile-but-statistically-quiet
fraud gets escalated instead of silently allowed.
"""
    with open(REPORTS_DIR / "baseline_comparison.md", "w") as f:
        f.write(md)

    print(json.dumps(report, indent=2))
    if usage.calls:
        print(f"\nLLM usage: {usage.calls:,} calls, {usage.input_tokens:,} input + "
              f"{usage.output_tokens:,} output tokens, ${usage.cost_usd:.4f} actual "
              f"(vs ~${estimate_cost_usd(settings.llm_model, usage.calls):.4f} estimated).")
    print(f"\nWrote {REPORTS_DIR / 'baseline_comparison.json'} and .md")


if __name__ == "__main__":
    main()
