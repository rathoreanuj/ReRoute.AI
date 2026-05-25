"""Structured itinerary / cascade shapes for APIs (parallel to trip.snapshot JSON)."""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field

from schema.trip_schemas import TripPublic


class TripLegPublic(BaseModel):
    id: str
    trip_id: str
    segment_order: int
    mode: str
    origin_code: str
    destination_code: str
    flight_number: str | None
    travel_date: str | None
    extra: dict[str, Any] | None = None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class ItinerarySegmentPublic(BaseModel):
    id: str
    trip_id: str
    segment_order: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class TripDetailPublic(BaseModel):
    """Trip row plus normalized legs & segments (from DB derivatives of snapshot)."""

    trip: TripPublic
    legs: list[TripLegPublic] = Field(default_factory=list)
    segments: list[ItinerarySegmentPublic] = Field(default_factory=list)


class HotelSegmentPayload(BaseModel):
    """Expected keys under snapshot.legs.hotel (validated when used from API)."""

    check_in_buffer_minutes: int | None = None


class MeetingSegmentPayload(BaseModel):
    scheduled_time_utc: str | None = None


class ConnectionSegmentPayload(BaseModel):
    departure_after_arrival_minutes: int | None = None


class WeatherSegmentPayload(BaseModel):
    origin_lat: float | None = None
    origin_lon: float | None = None
    destination_lat: float | None = None
    destination_lon: float | None = None


class CascadePreviewPublic(BaseModel):
    """Mirror of agent cascade_preview for typed UI consumption."""

    disruption_type: str | None = None
    missed_connection: bool | None = None
    hotel_update_message: str | None = None
    meeting_update_message: str | None = None
    what_we_changed_summary: list[str] = Field(default_factory=list)
