import uuid

from datetime import datetime
from decimal import Decimal

from app.infra.database.base import Base 
from sqlalchemy import DateTime, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.domain.enums import ProviderEnum


class Pricing(Base):
    __tablename__ = "pricing"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid7,
    )

    provider: Mapped[ProviderEnum] = mapped_column(String(255), nullable=False)

    model: Mapped[str] = mapped_column(String(255), nullable=False)

    price_per_1k_input_tokens: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=0,
    )

    price_per_1k_output_tokens: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=0,
    )

    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
