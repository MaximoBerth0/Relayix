"""Alembic environment.

The database URL and TLS mode come from `app.infra.config.settings`
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# every ORM model must be imported so Base.metadata is fully populated,
# otherwise autogenerate would treat missing tables as deletions.
import app.models.db.api_key  # noqa: F401
import app.models.db.pricing  # noqa: F401
import app.models.db.usage_record  # noqa: F401
from alembic import context
from app.infra.config import settings
from app.infra.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return settings.database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting ( `alembic upgrade head --sql` )."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect with the async engine and run the migrations."""
    config.set_main_option("sqlalchemy.url", _url())

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"ssl": settings.db_ssl},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
