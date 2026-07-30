"""initial schema: api_key, pricing, usage_record

Revision ID: 0001
Revises:
Create Date: 2026-07-29

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=True),
        sa.Column("monthly_token_quota", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_key_key_hash", "api_key", ["key_hash"], unique=True)

    op.create_table(
        "pricing",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("price_per_1k_input_tokens", sa.Numeric(12, 6), nullable=False),
        sa.Column("price_per_1k_output_tokens", sa.Numeric(12, 6), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "usage_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("token_in", sa.Integer(), nullable=False),
        sa.Column("token_out", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("cost", sa.Numeric(12, 6), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_key.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_record_api_key_id", "usage_record", ["api_key_id"])
    op.create_index("ix_usage_record_request_id", "usage_record", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_record_request_id", table_name="usage_record")
    op.drop_index("ix_usage_record_api_key_id", table_name="usage_record")
    op.drop_table("usage_record")
    op.drop_table("pricing")
    op.drop_index("ix_api_key_key_hash", table_name="api_key")
    op.drop_table("api_key")
