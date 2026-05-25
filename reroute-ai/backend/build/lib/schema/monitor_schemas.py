"""Monitor / dashboard aggregates for the authenticated user."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class MonitorTripSummary(BaseModel):
    trip_id: str
    title: str | None
    itinerary_revision: int
    pending_proposal_count: int
    last_disruption_kind: str | None = None
    last_disruption_at: datetime.datetime | None = None


class MonitorStatusResponse(BaseModel):
    generated_at: datetime.datetime
    trip_count: int
    trips_shown: int = Field(description="Summaries returned (capped for payload size)")
    total_pending_proposals: int
    trips: list[MonitorTripSummary] = Field(default_factory=list)


class MonitorTickResponse(BaseModel):
    ok: bool = True
    message: str
    status: MonitorStatusResponse
