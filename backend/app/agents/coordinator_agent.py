"""Coordinator agent: reconciles the three independent opinions into a
final verdict.

The architecture doc specifies two of these rules explicitly (hard policy
violation -> block; high anomaly + plausible context -> escalate) and
defers the rest to an external diagram this repo doesn't have. The
remaining two branches are filled in here so the table is fully specified,
built around one deliberate principle: escalation is exactly what happens
when the anomaly and context agents *disagree*. Agreement drives a
confident automatic decision (both bad -> block, both fine -> allow);
disagreement is what a human should look at. That's the PRD's core thesis
made concrete.

| policy | anomaly | context      | verdict  |
|--------|---------|--------------|----------|
| flag   | any     | any          | block    |
| clear  | high    | implausible  | block    |
| clear  | high    | plausible    | escalate |
| clear  | low     | implausible  | escalate |
| clear  | low     | plausible    | allow    |
"""
from dataclasses import dataclass

from app.agents.base import AgentOpinion


@dataclass(frozen=True)
class Verdict:
    final_verdict: str  # allow | escalate | block
    coordinator_reasoning: str


def run(anomaly: AgentOpinion, context: AgentOpinion, policy: AgentOpinion) -> Verdict:
    if policy.flag:
        return Verdict(
            "block",
            f"Blocked: policy_agent found a hard violation ({policy.reasoning}), which overrides the other agents.",
        )

    if anomaly.flag and context.flag:
        return Verdict(
            "block",
            "Blocked: anomaly_agent and context_agent independently agree this is suspicious "
            f"(anomaly: {anomaly.reasoning} | context: {context.reasoning}).",
        )

    if anomaly.flag and not context.flag:
        return Verdict(
            "escalate",
            "Escalated: anomaly_agent flagged this as statistically unusual, but context_agent finds it "
            f"plausible for this user -- the 'explainable anomaly' case a human should confirm "
            f"(anomaly: {anomaly.reasoning} | context: {context.reasoning}).",
        )

    if not anomaly.flag and context.flag:
        return Verdict(
            "escalate",
            "Escalated: anomaly_agent sees nothing statistically unusual, but context_agent finds it "
            f"doesn't fit this user's known behavior -- the 'unremarkable but off-profile' case a single "
            f"model would miss (anomaly: {anomaly.reasoning} | context: {context.reasoning}).",
        )

    return Verdict(
        "allow",
        f"Allowed: no policy violation, and anomaly_agent and context_agent agree this looks fine "
        f"(anomaly: {anomaly.reasoning} | context: {context.reasoning}).",
    )
