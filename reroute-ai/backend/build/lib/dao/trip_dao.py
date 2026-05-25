"""Data access: trips."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dao.base_dao import BaseDAO
from model.trip_model import Trip

_UPDATABLE = frozenset({"title", "snapshot", "itinerary_revision"})


class TripDAO(BaseDAO):
    def __init__(self, session: AsyncSession):
        super().__init__(Trip, session)

    async def get_by_id_for_user(self, *, trip_id: str, user_id: str) -> Trip | None:
        result = await self.session.execute(
            select(Trip).where(Trip.id == trip_id, Trip.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def count_for_user(self, *, user_id: str) -> int:
        q = select(func.count()).select_from(Trip).where(Trip.user_id == user_id)
        result = await self.session.execute(q)
        return int(result.scalar_one() or 0)

    async def list_for_user(self, *, user_id: str, limit: int | None = None) -> list[Trip]:
        q = select(Trip).where(Trip.user_id == user_id).order_by(Trip.created_at.desc())
        if limit is not None:
            q = q.limit(limit)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def list_all(self, *, offset: int = 0, limit: int = 50) -> list[Trip]:
        """Paginated list of all trips (monitor / background jobs).

        Oldest-updated first so trips that are not constantly edited still get scanned when a per-cycle cap applies.
        """
        q = (
            select(Trip)
            .order_by(Trip.updated_at.asc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        trip_id: str,
        user_id: str,
        title: str | None,
        snapshot: dict,
        itinerary_revision: int = 0,
    ) -> Trip:
        trip = Trip(
            id=trip_id,
            user_id=user_id,
            title=title,
            snapshot=snapshot,
            itinerary_revision=itinerary_revision,
        )
        self.session.add(trip)
        await self.session.flush()
        await self.session.refresh(trip)
        return trip

    async def update(self, trip: Trip, **fields: Any) -> Trip:
        """Set only provided columns (keys must be title | snapshot | itinerary_revision), then flush."""
        if unknown := set(fields) - _UPDATABLE:
            raise ValueError(f"Unsupported trip fields: {unknown}")
        for key, value in fields.items():
            setattr(trip, key, value)
        await self.session.flush()
        await self.session.refresh(trip)
        return trip

    async def bump_itinerary_revision(self, trip: Trip) -> Trip:
        trip.itinerary_revision = int(trip.itinerary_revision) + 1
        await self.session.flush()
        await self.session.refresh(trip)
        return trip

    async def delete(self, trip: Trip) -> None:
        await self.session.delete(trip)
        await self.session.flush()
