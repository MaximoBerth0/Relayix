"""Central application configuration.

Every tunable value in Relayix lives here. Nothing else in the codebase should
read os.environ directly: modules import `settings` 
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RELAYIX_",
        extra="ignore",
    )

    # app
    environment: str = "development"  # development | production
    debug: bool = False

    # the durable ledger (api_keys, usage_records, pricing) 
    database_url: str = "sqlite+aiosqlite:///./relayix.db" # future postgres database

    # provider credentials
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # how long to wait on an upstream provider before giving up (seconds).
    provider_timeout_s: float = 30.0

    # Fallback limit for keys that don't specify their own rate_limit_rpm
    default_rate_limit_rpm: int = 60

    # consecutive failures before a provider is taken out of rotation (open).
    circuit_breaker_fail_threshold: int = 5
    # how long to stay open before a single test request is allowed (half-open).
    circuit_breaker_reset_timeout_s: float = 30.0

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the singleton settings instance."""
    return Settings()


settings = get_settings()  
# setting() as a function so tests can override it