"""Data access: trip legs."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dao.base_dao import BaseDAO
from model.leg_model import TripLeg


class LegDAO(BaseDAO):
    def __init__(self, session: AsyncSession):
        super().__init__(TripLeg, session)

    async def delete_all_for_trip(self, *, trip_id: str) -> None:
        await self.session.execute(delete(TripLeg).where(TripLeg.trip_id == trip_id))
        await self.session.flush()

    async def list_for_trip(self, *, trip_id: str) -> list[TripLeg]:
        result = await self.session.execute(
            select(TripLeg)
            .where(TripLeg.trip_id == trip_id)
            .order_by(TripLeg.segment_order.asc(), TripLeg.created_at.asc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        trip_id: str,
        segment_order: int,
        mode: str,
        origin_code: str,
        destination_code: str,
        flight_number: str | None,
        travel_date: str | None,
        extra: dict | None,
        leg_id: str | None = None,
    ) -> TripLeg:
        row = TripLeg(
            id=leg_id or str(uuid.uuid4()),
            trip_id=trip_id,
            segment_order=segment_order,
            mode=mode,
            origin_code=origin_code,
            destination_code=destination_code,
            flight_number=flight_number,
            travel_date=travel_date,
            extra=extra,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row
