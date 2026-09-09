import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentOpinion, HumanReview, ReviewResult, Transaction

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


@router.get("/override-outcomes")
def override_outcomes(db: Session = Depends(get_db)):
    """What human reviewers did with the cases the coordinator escalated.

    This is the only ground-truth-adjacent signal the *live* system produces
    -- demo transactions carry no is_fraud label, so PRD 7.2's "false
    positive trend" cannot be computed directly here (the labelled version
    lives in /analytics/evaluation-summary, measured offline on the held-out
    PaySim split). A reviewer approving an escalated case is the closest live
    proxy for "the pipeline would have blocked something legitimate": the
    escalation was resolved as benign. It is a proxy, not a label -- the
    reviewer could be wrong, and only escalated cases are ever reviewed, so
    nothing here says anything about the allow/block cases no human saw.

    `decisions` counts the *standing* decision per case (the latest override
    on each), so re-reviewing a case corrects the picture instead of
    double-counting it. `total_overrides` is the raw event count.
    """
    escalated_total = db.execute(
        select(func.count()).select_from(ReviewResult).where(ReviewResult.final_verdict == "escalate")
    ).scalar_one()

    rows = db.execute(
        select(HumanReview.review_result_id, HumanReview.decision, HumanReview.reviewed_at)
        .join(ReviewResult, ReviewResult.id == HumanReview.review_result_id)
        .where(ReviewResult.final_verdict == "escalate")
    ).all()

    latest_by_case: dict[str, tuple] = {}
    for review_result_id, decision, reviewed_at in rows:
        current = latest_by_case.get(review_result_id)
        if current is None or reviewed_at >= current[1]:
            latest_by_case[review_result_id] = (decision, reviewed_at)

    decisions = {"approve": 0, "reject": 0}
    for decision, _ in latest_by_case.values():
        if decision in decisions:
            decisions[decision] += 1

    reviewed = len(latest_by_case)
    decided = decisions["approve"] + decisions["reject"]
    return {
        "escalated_total": escalated_total,
        "reviewed": reviewed,
        "pending": max(escalated_total - reviewed, 0),
        "review_rate": reviewed / escalated_total if escalated_total else 0.0,
        "total_overrides": len(rows),
        "decisions": decisions,
        "approve_rate": decisions["approve"] / decided if decided else 0.0,
    }


@router.get("/agent-flag-trend")
def agent_flag_trend(db: Session = Depends(get_db)):
    """Per-day flag rate for each agent (PRD 7.3: historical trend charts
    beyond 7.2's verdict mix).

    Flag *rate*, not flag count, so a busy day doesn't read as a riskier one.
    Each agent's series is independent -- divergence between them over time
    is the thing worth looking at, since the pipeline's whole premise is that
    the agents can disagree.
    """
    day = func.date(Transaction.occurred_at)
    stmt = (
        select(day, AgentOpinion.agent_name, AgentOpinion.flag, func.count())
        .join(AgentOpinion, AgentOpinion.transaction_id == Transaction.id)
        .group_by(day, AgentOpinion.agent_name, AgentOpinion.flag)
        .order_by(day)
    )

    totals: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for date_value, agent_name, flag, count in db.execute(stmt).all():
        bucket = totals[str(date_value)][agent_name]
        bucket[1] += count
        if flag:
            bucket[0] += count

    return [
        {
            "date": date_str,
            **{
                agent_name: (flagged / total if total else 0.0)
                for agent_name, (flagged, total) in sorted(agents.items())
            },
        }
        for date_str, agents in sorted(totals.items())
    ]


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
