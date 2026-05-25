"""List disruption / agent audit events for a trip."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from dao.disruption_event_dao import DisruptionEventDAO
from dao.trip_dao import TripDAO
from schema.disruption_schemas import DisruptionEventActivityPublic, DisruptionEventPublic


async def list_events_for_trip(
    *,
    session: AsyncSession,
    user_id: str,
    trip_id: str,
    limit: int = 100,
) -> list[DisruptionEventPublic]:
    trip_dao = TripDAO(session)
    if not await trip_dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    rows = await DisruptionEventDAO(session).list_for_trip_user(
        trip_id=trip_id, user_id=user_id, limit=limit
    )
    return [DisruptionEventPublic.model_validate(r) for r in rows]


async def list_activity_events_for_user(
    *,
    session: AsyncSession,
    user_id: str,
    limit: int = 200,
) -> list[DisruptionEventActivityPublic]:
    dao = DisruptionEventDAO(session)
    pairs = await dao.list_recent_for_user_with_trip_title(user_id=user_id, limit=limit)
    out: list[DisruptionEventActivityPublic] = []
    for ev, title in pairs:
        base = DisruptionEventPublic.model_validate(ev)
        out.append(DisruptionEventActivityPublic(**base.model_dump(), trip_title=title))
    return out
