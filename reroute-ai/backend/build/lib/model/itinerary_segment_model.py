"""ORM: non-bookable itinerary context (connection, hotel, meeting, weather) for cascade / tools."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from model.base import Base


class ItinerarySegment(Base):
    __tablename__ = "itinerary_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("trips.id", ondelete="CASCADE"),
        index=True,
    )
    segment_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
