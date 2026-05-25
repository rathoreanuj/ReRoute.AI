"""API schemas: disruption audit events."""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field


class DisruptionEventPublic(BaseModel):
    id: str
    trip_id: str
    kind: str
    disruption_type: str | None
    proposal_id: str | None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class DisruptionEventActivityPublic(DisruptionEventPublic):
    """Cross-trip feed: includes trip title from join (avoids N+1 client fetches)."""

    trip_title: str | None = None
