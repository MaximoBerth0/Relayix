import uuid

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from app.infra.database.base import Base
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.domain.enums import ProviderEnum

if TYPE_CHECKING:
    from app.models.db.api_key import Api_Key


class Usage_Record(Base):
    __tablename__ = "usage_record"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid7,
    )

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("api_key.id"),
        index=True,
        nullable=False,
    )

    provider: Mapped[ProviderEnum] = mapped_column(String(255), nullable=False)

    model: Mapped[str] = mapped_column(String(255), nullable=False)

    token_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    token_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=0,
    )

    request_id: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    api_key: Mapped["Api_Key"] = relationship(
        back_populates="usage_records",
    )
