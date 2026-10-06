from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute, not CWD-relative: ml/ scripts and the backend app are run from
# different working directories, and a relative sqlite:///./ path would
# silently create two divergent DB files depending on where you run from.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SQLITE_URL = f"sqlite:///{REPO_ROOT / 'fraud_analyzer.db'}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_SQLITE_URL

    # "mock" | "openai" | "anthropic". Keys are server-side only -- nothing
    # here is ever sent to the frontend (the browser only gets VITE_* vars,
    # see frontend/.env.example), and no route echoes them back.
    llm_provider: str = "mock"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    # Must be a model id present in llm_usage.MODEL_PRICING, or the cost
    # guards can't price it. No date suffix -- a suffixed id is a different
    # string to the provider and 404s.
    llm_model: str = "gpt-5.6-luna"

    # Hard spend caps for THIS project, enforced from the persistent
    # llm_daily_spend ledger (UTC days/months) so they survive restarts.
    # The OpenAI console limit is org-wide and shared across projects, so
    # it can't stop this demo draining the budget the others depend on.
    # Default: a $5/month key split four ways -> $1.25. The daily cap stops
    # one bad day from eating the month; $0.15 still fits a worst-case
    # demo re-seed (~$0.13). When either is hit, the context agent falls
    # back to the mock heuristic with an explicit budget label.
    llm_monthly_budget_usd: float = 1.25
    llm_daily_budget_usd: float = 0.15

    anomaly_high_threshold: float = 0.5

    review_rate_limit: str = "20/minute"
    frontend_origin: str = "http://localhost:5173"

    # Application log level. INFO is the default because the per-call LLM
    # token/cost lines are logged at INFO, and they are the only visibility
    # into production spend short of querying the ledger directly.
    log_level: str = "INFO"

    # Phase 2: reviewer auth. Backend verifies tokens against Supabase's Auth
    # API (auth.get_user), not a local JWT decode, so only these two are
    # needed -- no shared JWT secret to keep in sync with signing-key rotation.
    supabase_url: str = ""
    supabase_anon_key: str = ""


settings = Settings()
