"""
docker compose -f docker/docker-compose-test.yml up -d
pytest testing/ -v
docker compose -f docker/docker-compose-test.yml down
"""

import os

# environment variables, must be set before any app import.
# app.infra.config builds `settings` at import time
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "true"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://user:password@localhost:5432/test_db"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
os.environ["DEFAULT_RATE_LIMIT_RPM"] = "60"

# clear settings cache so test env vars take effect
from app.infra.config import get_settings

get_settings.cache_clear()

# model imports, so Base.metadata knows about every table
from datetime import UTC, datetime, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models.db.api_key
import app.models.db.pricing
import app.models.db.usage_record
from app.core.accounting.pricing import build_pricing_table
from app.core.adapters.base import ProviderAdapter
from app.core.adapters.registry import build_registry
from app.core.ratelimit.resilient import ResilientRateLimiter
from app.core.ratelimit.token_bucket import InMemoryRateLimiter
from app.core.resilience.circuit_breaker import CircuitBreaker
from app.core.routing.router import build_router
from app.infra.config import settings
from app.infra.database.base import Base
from app.infra.database.session import get_session
from app.infra.ratelimit.redis_client import build_redis_client
from app.infra.ratelimit.redis_limiter import RedisRateLimiter
from app.infra.security.crypto import hash_api_key
from app.main import app
from app.models.db.api_key import Api_Key
from app.models.db.pricing import Pricing
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import ProviderEnum
from app.repositories.pricing_repo import load_pricing_rates

# engine + session factory

""" looping issue: 
NullPool: pytest-asyncio runs each test on a fresh event loop, but pooled
asyncpg connections are bound to the loop that created them. Reusing a pooled
connection on a later test's loop raises "attached to a different loop", so
disable pooling and open a new connection per test.
"""


engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)

# db_session
# One connection per test. The schema is created and committed, then a single
# OUTER transaction is opened and rolled back at the end.


@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with engine.connect() as conn:
        # create schema and commit it so it survives the outer-transaction rollback
        await conn.run_sync(Base.metadata.create_all)
        await conn.commit()

        outer = await conn.begin()

        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        yield session

        await session.close()
        await outer.rollback()  # discard everything the test wrote
        await conn.run_sync(Base.metadata.drop_all)
        await conn.commit()


# redis_client: real client against the test container, flushed on both sides so a
# leftover token bucket or idempotency key can never leak between tests.


@pytest_asyncio.fixture(scope="function")
async def redis_client():
    client = build_redis_client()
    await client.flushdb()

    yield client

    await client.flushdb()
    await client.aclose()


# client: HTTP client wired to the test DB session and the test Redis.

@pytest_asyncio.fixture
async def client(db_session, redis_client):
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    
    app.state.registry = build_registry()
    app.state.router = build_router()
    app.state.pricing = build_pricing_table(await load_pricing_rates(db_session))
    app.state.redis = redis_client
    app.state.rate_limiter = ResilientRateLimiter(
        primary=RedisRateLimiter(redis_client),
        fallback=InMemoryRateLimiter(),
        breaker=CircuitBreaker(
            fail_threshold=settings.ratelimit_breaker_fail_threshold,
            reset_timeout_s=settings.ratelimit_breaker_reset_timeout_s,
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# End-to-end flow helpers

# The request path is entirely real (auth, rate limit, routing, gateway, usage
# billing, serialization) except for the single seam that leaves the process:
# the provider's outbound HTTP call. StubAdapter replaces that seam so a test
# can drive the whole flow without touching OpenAI/Anthropic.

# plaintext bearer token whose sha256 is seeded into the api_key table.
API_TOKEN = "relayix-test-token"
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


class StubAdapter(ProviderAdapter):
    """A ProviderAdapter that returns a canned response instead of calling out"""

    def __init__(self, provider: ProviderEnum, content: str = "stubbed reply") -> None:
        self._provider = provider
        self._content = content
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(
            provider=self._provider,
            model=request.model,
            content=self._content,
            tokens_in=12,
            tokens_out=8,
            finish_reason="stop",
            request_id=f"stub-{len(self.calls)}",
        )


class FailingAdapter(ProviderAdapter):
    """A ProviderAdapter that always raises, to drive failover and error paths.

    Records requests on `.calls` so a test can prove the provider was actually
    attempted before failing over to the next candidate.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        raise self._error


@pytest_asyncio.fixture
async def seed_api_key(db_session) -> Api_Key:
    """Insert an active API key whose bearer token is `API_TOKEN`."""
    api_key = Api_Key(
        name="test-key",
        key_hash=hash_api_key(API_TOKEN),
        is_active=True,
    )
    db_session.add(api_key)
    await db_session.flush()
    return api_key


@pytest_asyncio.fixture
async def seed_pricing(db_session) -> None:
    """Insert pricing rows for the models the default catalog can route to,
    so UsageRecorder can price a response instead of raising PricingRateNotFound.
    """
    rows = [
        Pricing(
            provider=ProviderEnum.OPENAI.value,
            model="gpt-4o",
            price_per_1k_input_tokens=Decimal("0.0025"),
            price_per_1k_output_tokens=Decimal("0.010"),
            effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ),
        Pricing(
            provider=ProviderEnum.ANTHROPIC.value,
            model="claude-sonnet-5",
            price_per_1k_input_tokens=Decimal("0.003"),
            price_per_1k_output_tokens=Decimal("0.015"),
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()


@pytest_asyncio.fixture
async def stub_openai(db_session, seed_api_key, seed_pricing, client) -> StubAdapter:
    """A fully wired flow client backed by a stubbed OpenAI provider."""
    app.state.pricing = build_pricing_table(await load_pricing_rates(db_session))
    adapter = StubAdapter(ProviderEnum.OPENAI)
    app.state.registry.register(ProviderEnum.OPENAI, adapter)
    return adapter
