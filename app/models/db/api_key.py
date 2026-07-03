import uuid

from datetime import datetime
from typing import TYPE_CHECKING
from app.infra.database.base import Base
from sqlalchemy import Boolean, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.db.usage_record import Usage_Record

class Api_Key(Base):
    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid7,
    )

    key_hash: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    rate_limit_rpm: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )  # per-key throttle

    monthly_token_quota: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )  # optional spend/token cap

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    usage_records: Mapped[list["Usage_Record"]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
    )