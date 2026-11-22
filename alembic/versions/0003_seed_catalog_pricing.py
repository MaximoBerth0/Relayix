"""seed pricing for every catalog model, so startup validation passes on a fresh db

Rows are only inserted for (provider, model) pairs that have no rate yet, so
rates loaded by hand are left alone.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EFFECTIVE_FROM = "2026-01-01T00:00:00+00:00"

# USD per 1K tokens: (provider, model, input, output)
RATES = [
    ("openai", "gpt-4o", "0.0025", "0.010"),
    ("openai", "gpt-4o-mini", "0.00015", "0.0006"),
    ("openai", "o3", "0.010", "0.040"),
    ("anthropic", "claude-sonnet-5", "0.003", "0.015"),
    ("anthropic", "claude-opus-4-8", "0.005", "0.025"),
    ("anthropic", "claude-haiku-4-5", "0.001", "0.005"),
]


def upgrade() -> None:
    insert = sa.text(
        """
        INSERT INTO pricing (id, provider, model, price_per_1k_input_tokens,
                             price_per_1k_output_tokens, effective_from)
        SELECT gen_random_uuid(), :provider, :model, CAST(:input AS numeric),
               CAST(:output AS numeric), CAST(:effective_from AS timestamptz)
        WHERE NOT EXISTS (
            SELECT 1 FROM pricing WHERE provider = :provider AND model = :model
        )
        """
    )
    for provider, model, price_in, price_out in RATES:
        op.execute(
            insert.bindparams(
                provider=provider,
                model=model,
                input=price_in,
                output=price_out,
                effective_from=EFFECTIVE_FROM,
            )
        )


def downgrade() -> None:
    delete = sa.text(
        "DELETE FROM pricing WHERE provider = :provider AND model = :model "
        "AND effective_from = CAST(:effective_from AS timestamptz)"
    )
    for provider, model, _, _ in RATES:
        op.execute(delete.bindparams(provider=provider, model=model, effective_from=EFFECTIVE_FROM))
