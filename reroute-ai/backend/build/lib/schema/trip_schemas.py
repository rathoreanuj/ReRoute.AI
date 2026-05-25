"""API schemas for trip CRUD (snapshot JSON is agent contract)."""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field


class TripCreateRequest(BaseModel):
    title: str | None = Field(None, max_length=255)
    snapshot: dict[str, Any] = Field(
        ...,
        description="Agent trip snapshot; server sets snapshot.trip_id to the new row id.",
    )


class TripUpdateRequest(BaseModel):
    title: str | None = Field(None, max_length=255)
    snapshot: dict[str, Any] | None = None
    itinerary_revision: int | None = Field(None, ge=0)


class TripPublic(BaseModel):
    id: str
    user_id: str
    title: str | None
    snapshot: dict[str, Any]
    itinerary_revision: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}
