"""
docker compose -f docker/docker-compose-test.yml up -d
pytest tests/ -v
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
import pytest
import pytest_asyncio
from fixtures.adapters import StubAdapter
from fixtures.factories import make_api_key, make_pricing_rows
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models.db.api_key
import app.models.db.pricing
import app.models.db.usage_record
from app.core.accounting.pricing import build_pricing_table
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
from app.main import app
from app.models.db.api_key import Api_Key
from app.models.db.usage_record import Usage_Record
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
# the provider's outbound HTTP call. StubAdapter (fixtures/adapters.py) replaces
# that seam so a test can drive the whole flow without touching OpenAI/Anthropic.


@pytest_asyncio.fixture
async def seed_api_key(db_session) -> Api_Key:
    """Insert an active API key whose bearer token is `API_TOKEN`."""
    api_key = make_api_key()
    db_session.add(api_key)
    await db_session.flush()
    return api_key


@pytest_asyncio.fixture
async def seed_pricing(db_session) -> None:
    """Insert pricing rows for the models the default catalog can route to,
    so UsageRecorder can price a response instead of raising PricingRateNotFound.
    """
    db_session.add_all(make_pricing_rows())
    await db_session.flush()


@pytest_asyncio.fixture
async def stub_openai(db_session, seed_api_key, seed_pricing, client) -> StubAdapter:
    """A fully wired flow client backed by a stubbed OpenAI provider."""
    app.state.pricing = build_pricing_table(await load_pricing_rates(db_session))
    adapter = StubAdapter(ProviderEnum.OPENAI)
    app.state.registry.register(ProviderEnum.OPENAI, adapter)
    return adapter


@pytest.fixture
def usage_count(db_session):
    """`await usage_count()` -> rows in the usage ledger, for tests that assert a
    request was billed exactly once (or never).
    """

    async def _count() -> int:
        return await db_session.scalar(select(func.count()).select_from(Usage_Record))

    return _count
