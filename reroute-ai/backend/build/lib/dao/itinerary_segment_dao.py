"""Data access: itinerary segments (cascade / weather context)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dao.base_dao import BaseDAO
from model.itinerary_segment_model import ItinerarySegment


class ItinerarySegmentDAO(BaseDAO):
    def __init__(self, session: AsyncSession):
        super().__init__(ItinerarySegment, session)

    async def delete_all_for_trip(self, *, trip_id: str) -> None:
        await self.session.execute(delete(ItinerarySegment).where(ItinerarySegment.trip_id == trip_id))
        await self.session.flush()

    async def list_for_trip(self, *, trip_id: str) -> list[ItinerarySegment]:
        result = await self.session.execute(
            select(ItinerarySegment)
            .where(ItinerarySegment.trip_id == trip_id)
            .order_by(ItinerarySegment.segment_order.asc(), ItinerarySegment.created_at.asc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        trip_id: str,
        segment_order: int,
        kind: str,
        payload: dict,
        segment_id: str | None = None,
    ) -> ItinerarySegment:
        row = ItinerarySegment(
            id=segment_id or str(uuid.uuid4()),
            trip_id=trip_id,
            segment_order=segment_order,
            kind=kind,
            payload=payload,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row
