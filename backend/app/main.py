import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.agents.context_agent import LLMConfigurationError
from app.config import settings
from app.db import init_db
from app.rate_limit import limiter
from app.routers import analytics, health, reviews, transactions

logger = logging.getLogger(__name__)

# Uvicorn configures handlers for its own loggers only, so application
# loggers propagate to a bare root logger -- and with no root handler
# Python's fallback emits WARNING and above, silently dropping every INFO
# record. That meant the per-call "llm_call ... cost_usd=..." lines never
# reached the Render log stream, which is the only window into live spend.
# basicConfig is a no-op when the root logger already has handlers, so this
# is safe under any host that does configure logging itself.
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Fraud Investigation Squad",
    description="Demo mode -- synthetic data only.",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(LLMConfigurationError)
async def llm_configuration_error_handler(request: Request, exc: LLMConfigurationError):
    """A misconfigured LLM provider is an operator error, not a client error.

    Returned as 503 with the reason, rather than a bare 500 or -- worse -- a
    200 carrying a mock verdict the caller would have no way to distinguish
    from a real one. The message names the misconfiguration but never the
    key itself.
    """
    logger.error("LLM misconfiguration on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "hint": "Set LLM_PROVIDER=mock to serve the deterministic heuristic instead.",
        },
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(transactions.router)
app.include_router(reviews.router)
app.include_router(analytics.router)
