"""ORM: bookable / move legs (flight, train, ground) for a trip."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from model.base import Base


class TripLeg(Base):
    __tablename__ = "trip_legs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("trips.id", ondelete="CASCADE"),
        index=True,
    )
    segment_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="flight")
    origin_code: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_code: Mapped[str] = mapped_column(String(16), nullable=False)
    flight_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    travel_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
