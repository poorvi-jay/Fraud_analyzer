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

    llm_provider: str = "mock"  # "mock" | "anthropic"
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"

    anomaly_high_threshold: float = 0.5

    review_rate_limit: str = "20/minute"
    frontend_origin: str = "http://localhost:5173"

    # Phase 2: reviewer auth. Backend verifies tokens against Supabase's Auth
    # API (auth.get_user), not a local JWT decode, so only these two are
    # needed -- no shared JWT secret to keep in sync with signing-key rotation.
    supabase_url: str = ""
    supabase_anon_key: str = ""


settings = Settings()
