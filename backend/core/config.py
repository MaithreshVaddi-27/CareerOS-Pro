"""Env-driven config. No secrets hard-coded. Per spec section 51/52."""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# RawJobPosting imported via schemas - removed unused import


class Settings(BaseSettings):
    app_env: str = "development"

    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_database: str = "careeros"
    mysql_user: str = "careeros"
    # mysql_password: str must be explicitly set - no default for security
    mysql_password: str

    database_url: str = ""

    jsearch_api_key: str = ""
    job_api_mode: str = "mock"  # "mock" | "live"

    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "in"  # gb, us, in, etc — see Adzuna docs for supported codes

    job_sources: str = "jsearch,adzuna,remotive,remoteok,arbeitnow,company"  # comma-separated, used when job_api_mode=live

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"  # alias, auto-follows Google rollouts — never pin exact version
    llama_cpp_base_url: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3.5-sonnet"

    # NVIDIA NIM
    nvidia_api_key: str = ""
    nvidia_model: str = "meta/llama-3.1-70b-instruct"

    qdrant_url: str = ""

    firecrawl_api_key: str = ""

    # api_key: str must be explicitly set to enable API key protection - empty = open (INSECURE)
    api_key: str
    rate_limit_per_minute: int = 60

    # Shared rate-limit counter store. Empty keeps the in-process limiter, which is
    # correct for a single worker but means N workers enforce N x the limit. Set
    # this (e.g. redis://redis:6379/0) before running multiple workers.
    redis_url: str = ""

    # Refuse to start instead of silently falling back to in-memory checkpoints when
    # MySQL checkpointing fails. Off by default so local dev still works; turn on in
    # production where losing checkpoints across restarts actually matters.
    checkpoint_strict: bool = False

    # Comma-separated list of allowed CORS origins. Defaults cover local dev
    # (Vite on 5173, CRA-style on 3000) so nothing breaks out of the box —
    # override in production with your real frontend origin(s), e.g.
    # ALLOWED_ORIGINS=https://app.careeros.example
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


    llm_provider_mode: str = "auto"  # "auto" | "llama" | "gemini" | "openrouter" | "nvidia" | "none"

    # Company scraper configuration
    # Comma-separated paths probed under the company domain, highest priority
    # first. Extend this to teach the scraper a new ATS layout without code.
    company_career_page_patterns: str = ""
    company_scraper_cache_size: int = 100  # Max entries in HTML cache
    company_scraper_cache_ttl_seconds: int = 900  # 0 disables expiry
    company_scraper_max_concurrency: int = 5  # Max concurrent scraper requests per domain
    company_scraper_jitter_max: float = 0.5  # Max seconds of jitter added to retry delays
    company_scraper_timeout_seconds: float = 20.0  # Per-request HTTP timeout
    company_scraper_max_retries: int = 2  # Retries after the first attempt
    company_scraper_max_url_probes: int = 4  # Career URL patterns probed per company
    company_scraper_user_agent: str = (
        "CareerOS/1.0 (+https://github.com/MaithreshVaddi-27/CareerOS-Pro)"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def resolved_database_url(self) -> str:
        # Always use MySQL - never fall back to SQLite
        if self.database_url:
            return self.database_url

        # Build MySQL URL from components
        if all([self.mysql_host, self.mysql_port, self.mysql_database, self.mysql_user]):
            # URL-encode the password to handle special characters like @
            from urllib.parse import quote_plus

            encoded_password = quote_plus(self.mysql_password)
            return (
                f"mysql+pymysql://{self.mysql_user}:{encoded_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            )

        # If neither is properly configured, raise an error
        raise ValueError(
            "MySQL database not properly configured. Either set DATABASE_URL or provide "
            "all of MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, and MYSQL_USER."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()