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
        extra="ignore",
    )

    # app
    environment: str = "development"  # development | production
    debug: bool = False

    # the durable ledger (api_keys, usage_records, pricing)
    database_url: str 

    # async engine / connection-pool tuning (consumed by database/session.py)
    db_echo: bool = False             # log every emitted SQL statement
    db_pool_size: int = 5             # persistent connections kept open
    db_max_overflow: int = 10         # extra connections allowed past pool_size under load
    db_pool_timeout_s: float = 30.0   # how long to wait for a free connection before erroring
    db_pool_recycle_s: int = 1800     # recycle connections older than this (avoids stale sockets)
    db_pool_pre_ping: bool = True     # test a connection with a ping before handing it out

    # per-connection timeouts (asyncpg / PostgreSQL specific)
    db_connect_timeout_s: float = 10.0    # give up establishing a new connection after this
    db_command_timeout_s: float = 30.0    # asyncpg client-side query timeout
    db_statement_timeout_ms: int = 30000  # server-side PostgreSQL statement_timeout (milliseconds)

    # provider credentials
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # how long to wait on an upstream provider before giving up (seconds).
    provider_timeout_s: float = 30.0

    # Fallback limit for keys that don't specify their own rate_limit_rpm
    default_rate_limit_rpm: int = 60

    # --- rate limiting (Redis primary, in-memory fallback) ---
    # where the shared token buckets live. Redis is the source of truth so the
    # limit is enforced globally across every worker/instance.
    redis_url: str = "redis://localhost:6379/0"
    redis_connect_timeout_s: float = 2.0   # give up establishing a connection after this
    redis_command_timeout_s: float = 2.0   # give up on a single command after this
    redis_max_connections: int = 20        # connection pool ceiling

    # circuit breaker guarding Redis itself: when Redis is unreachable we stop
    # calling it (open) and serve from the in-memory limiter until it recovers.
    ratelimit_breaker_fail_threshold: int = 3
    ratelimit_breaker_reset_timeout_s: float = 10.0

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