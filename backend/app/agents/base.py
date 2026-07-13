from dataclasses import dataclass


@dataclass(frozen=True)
class AgentOpinion:
    agent_name: str
    score: float  # 0-1, higher = more suspicious
    flag: bool
    reasoning: str
