"""Seed the minimum rows the gateway needs to serve a request locally.

    docker compose -f docker/docker-compose.yml exec api python -m scripts.seed_dev
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

# Api_Key.usage_records points at "Usage_Record" by name, so the module has to be
# imported before the mapper can be configured.
import app.models.db.usage_record  # noqa: F401
from app.core.routing.router import MODEL_REGISTRY
from app.infra.database.session import AsyncSessionLocal, engine
from app.infra.security.crypto import hash_api_key
from app.models.db.api_key import Api_Key
from app.models.db.pricing import Pricing

# Overridable so a developer can seed their own token: DEV_API_TOKEN=...
DEV_API_TOKEN = os.getenv("DEV_API_TOKEN", "relayix-dev-token")

# far enough in the past that PricingTable.get() always finds it in effect
EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=UTC)


async def seed_api_key(session) -> str:
    """Insert the dev API key if it isn't there yet. Returns a status line."""
    key_hash = hash_api_key(DEV_API_TOKEN)
    existing = await session.scalar(select(Api_Key).where(Api_Key.key_hash == key_hash))
    if existing is not None:
        return f"api_key: already present (id={existing.id})"

    api_key = Api_Key(name="dev-key", key_hash=key_hash, is_active=True)
    session.add(api_key)
    await session.flush()
    return f"api_key: created (id={api_key.id})"


async def seed_pricing(session) -> str:
    """Insert a pricing row for every model in MODEL_REGISTRY that lacks one."""
    existing = {
        (provider, model)
        for provider, model in await session.execute(
            select(Pricing.provider, Pricing.model)
        )
    }

    created = 0
    for model, meta in MODEL_REGISTRY.items():
        if (meta.provider.value, model) in existing:
            continue
        session.add(
            Pricing(
                provider=meta.provider.value,
                model=model,
                price_per_1k_input_tokens=Decimal(str(meta.input_cost)),
                price_per_1k_output_tokens=Decimal(str(meta.output_cost)),
                effective_from=EFFECTIVE_FROM,
            )
        )
        created += 1

    return f"pricing: {created} created, {len(existing)} already present"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        print(await seed_api_key(session))
        print(await seed_pricing(session))
        await session.commit()

    await engine.dispose()
    print(f"\nbearer token: {DEV_API_TOKEN}")


if __name__ == "__main__":
    asyncio.run(main())
