"""ORM: agent rebooking proposal (awaiting user confirm)."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from model.base import Base


class RebookingProposal(Base):
    __tablename__ = "rebooking_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("trips.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    context: Mapped[dict] = mapped_column(JSON, nullable=False)
    selected_offer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duffel_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
