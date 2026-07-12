from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infra.config import settings

# engine with connection pooling
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_s,
    pool_recycle=settings.db_pool_recycle_s,
    pool_pre_ping=settings.db_pool_pre_ping,
    # connection timeouts (asyncpg specific)
    connect_args={
        "timeout": settings.db_connect_timeout_s,  # connection timeout
        "command_timeout": settings.db_command_timeout_s,  # query timeout
        "ssl": "require",
        "server_settings": {
            "statement_timeout": str(
                settings.db_statement_timeout_ms
            ),  # PostgreSQL timeout
        },
    },
)

# session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# dependency fastAPI (transactions)
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()  # ← auto-commit on success
        except Exception:
            await session.rollback()  # ← auto-rollback on error
            raise
        finally:
            await session.close()


# lifecycle management
async def startup() -> None:
    """Test database connection on startup"""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))


async def shutdown() -> None:
    """Dispose engine on shutdown"""
    await engine.dispose()

