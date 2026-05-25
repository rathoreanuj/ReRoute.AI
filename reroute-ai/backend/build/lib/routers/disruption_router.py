"""HTTP: disruption / agent audit trail per trip."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from deps import get_current_user
from model.user_model import User
from schema.disruption_schemas import DisruptionEventActivityPublic, DisruptionEventPublic
from service import disruption_service

router = APIRouter(prefix="/disruptions", tags=["disruptions"])


@router.get(
    "/events",
    response_model=list[DisruptionEventActivityPublic],
    status_code=status.HTTP_200_OK,
)
async def list_my_activity_events(
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[DisruptionEventActivityPublic]:
    """Recent disruption events across all of the current user's trips (single query)."""
    return await disruption_service.list_activity_events_for_user(
        session=session,
        user_id=current.id,
        limit=limit,
    )


@router.get(
    "/trips/{trip_id}/events",
    response_model=list[DisruptionEventPublic],
    status_code=status.HTTP_200_OK,
)
async def list_trip_events(
    trip_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[DisruptionEventPublic]:
    return await disruption_service.list_events_for_trip(
        session=session,
        user_id=current.id,
        trip_id=trip_id,
        limit=limit,
    )
