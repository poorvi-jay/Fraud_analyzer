import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentOpinion, ReviewResult, Transaction

router = APIRouter(prefix="/analytics", tags=["analytics"])

REPORT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "ml" / "reports" / "baseline_comparison.json"

# Same order the coordinator's decision table (app/agents/coordinator_agent.py)
# is built around: escalation is defined as anomaly/context disagreement, so
# that pair is listed first as the one the PRD's thesis is actually about.
AGENT_PAIRS = [
    ("anomaly_agent", "context_agent"),
    ("anomaly_agent", "policy_agent"),
    ("context_agent", "policy_agent"),
]


@router.get("/evaluation-summary")
def evaluation_summary():
    if not REPORT_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No baseline comparison report yet -- run ml/evaluate_baseline.py first.",
        )
    return json.loads(REPORT_PATH.read_text())


@router.get("/verdict-distribution")
def verdict_distribution(db: Session = Depends(get_db)):
    stmt = select(ReviewResult.final_verdict, func.count()).group_by(ReviewResult.final_verdict)
    rows = db.execute(stmt).all()
    return [{"verdict": verdict, "count": count} for verdict, count in rows]


@router.get("/agent-agreement-rate")
def agent_agreement_rate(db: Session = Depends(get_db)):
    stmt = select(AgentOpinion.transaction_id, AgentOpinion.agent_name, AgentOpinion.flag)
    rows = db.execute(stmt).all()

    by_transaction: dict[str, dict[str, bool]] = defaultdict(dict)
    for transaction_id, agent_name, flag in rows:
        by_transaction[transaction_id][agent_name] = flag

    overall_agree = 0
    overall_total = 0
    pair_agree = {pair: 0 for pair in AGENT_PAIRS}
    pair_total = {pair: 0 for pair in AGENT_PAIRS}

    for flags in by_transaction.values():
        if len(flags) < 3:
            continue  # incomplete opinion set, skip rather than misrepresent agreement
        overall_total += 1
        if len(set(flags.values())) == 1:
            overall_agree += 1
        for pair in AGENT_PAIRS:
            a, b = pair
            pair_total[pair] += 1
            if flags[a] == flags[b]:
                pair_agree[pair] += 1

    return {
        "overall": {
            "agree": overall_agree,
            "disagree": overall_total - overall_agree,
            "total": overall_total,
            "rate": overall_agree / overall_total if overall_total else 0.0,
        },
        "pairs": [
            {
                "agents": list(pair),
                "agree": pair_agree[pair],
                "total": pair_total[pair],
                "rate": pair_agree[pair] / pair_total[pair] if pair_total[pair] else 0.0,
            }
            for pair in AGENT_PAIRS
        ],
    }


@router.get("/verdict-trend")
def verdict_trend(db: Session = Depends(get_db)):
    day = func.date(Transaction.occurred_at)
    stmt = (
        select(day, ReviewResult.final_verdict, func.count())
        .join(ReviewResult, ReviewResult.transaction_id == Transaction.id)
        .group_by(day, ReviewResult.final_verdict)
        .order_by(day)
    )
    rows = db.execute(stmt).all()

    by_date: dict[str, dict[str, int]] = defaultdict(lambda: {"allow": 0, "escalate": 0, "block": 0})
    for date_value, verdict, count in rows:
        by_date[str(date_value)][verdict] = count

    return [{"date": date_str, **counts} for date_str, counts in sorted(by_date.items())]
