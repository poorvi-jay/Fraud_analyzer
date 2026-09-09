from fastapi import APIRouter

from app.agents import context_agent

router = APIRouter()


@router.get("/health")
def health():
    """Liveness plus the one piece of configuration a visitor can't otherwise
    verify: whether the context agent is really LLM-backed on this
    deployment, or running the mock heuristic. See context_agent.provider_status.
    """
    return {"status": "ok", "context_agent": context_agent.provider_status()}
