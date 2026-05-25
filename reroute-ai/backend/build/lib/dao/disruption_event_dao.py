"""Data access: disruption / agent audit events."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dao.base_dao import BaseDAO
from model.disruption_event_model import DisruptionEvent
from model.trip_model import Trip


class DisruptionEventDAO(BaseDAO):
    def __init__(self, session: AsyncSession):
        super().__init__(DisruptionEvent, session)

    async def create(
        self,
        *,
        trip_id: str,
        user_id: str,
        kind: str,
        disruption_type: str | None,
        proposal_id: str | None,
        payload: dict,
        event_id: str | None = None,
    ) -> DisruptionEvent:
        row = DisruptionEvent(
            id=event_id or str(uuid.uuid4()),
            trip_id=trip_id,
            user_id=user_id,
            kind=kind,
            disruption_type=disruption_type,
            proposal_id=proposal_id,
            payload=payload,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_for_trip_user(
        self,
        *,
        trip_id: str,
        user_id: str,
        limit: int = 100,
    ) -> list[DisruptionEvent]:
        result = await self.session.execute(
            select(DisruptionEvent)
            .where(DisruptionEvent.trip_id == trip_id, DisruptionEvent.user_id == user_id)
            .order_by(DisruptionEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def latest_for_trip_user(self, *, trip_id: str, user_id: str) -> DisruptionEvent | None:
        result = await self.session.execute(
            select(DisruptionEvent)
            .where(DisruptionEvent.trip_id == trip_id, DisruptionEvent.user_id == user_id)
            .order_by(DisruptionEvent.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_for_trip_by_kind(self, *, trip_id: str, kind: str) -> DisruptionEvent | None:
        result = await self.session.execute(
            select(DisruptionEvent)
            .where(DisruptionEvent.trip_id == trip_id, DisruptionEvent.kind == kind)
            .order_by(DisruptionEvent.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_by_kind_for_trip_ids(
        self, *, trip_ids: list[str], kind: str
    ) -> dict[str, DisruptionEvent | None]:
        """One grouped query: latest row per trip_id for the given kind (monitor cycle throttle)."""
        if not trip_ids:
            return {}
        sub = (
            select(
                DisruptionEvent.trip_id.label("tid"),
                func.max(DisruptionEvent.created_at).label("mx"),
            )
            .where(
                DisruptionEvent.trip_id.in_(trip_ids),
                DisruptionEvent.kind == kind,
            )
            .group_by(DisruptionEvent.trip_id)
        ).subquery()
        stmt = select(DisruptionEvent).join(
            sub,
            (DisruptionEvent.trip_id == sub.c.tid) & (DisruptionEvent.created_at == sub.c.mx),
        ).where(DisruptionEvent.kind == kind)
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        out: dict[str, DisruptionEvent | None] = {tid: None for tid in trip_ids}
        for ev in rows:
            out[ev.trip_id] = ev
        return out

    async def list_recent_for_user_with_trip_title(
        self, *, user_id: str, limit: int = 200
    ) -> list[tuple[DisruptionEvent, str | None]]:
        """Newest events across all of the user's trips, with trip title (single query)."""
        lim = min(max(limit, 1), 500)
        stmt = (
            select(DisruptionEvent, Trip.title)
            .join(Trip, Trip.id == DisruptionEvent.trip_id)
            .where(DisruptionEvent.user_id == user_id)
            .order_by(DisruptionEvent.created_at.desc())
            .limit(lim)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def latest_per_trip_for_user(
        self, *, user_id: str, trip_ids: list[str]
    ) -> dict[str, DisruptionEvent | None]:
        """One query: latest event per trip_id (for monitor dashboard)."""
        if not trip_ids:
            return {}
        mx = (
            select(
                DisruptionEvent.trip_id.label("tid"),
                func.max(DisruptionEvent.created_at).label("mx_at"),
            )
            .where(
                DisruptionEvent.user_id == user_id,
                DisruptionEvent.trip_id.in_(trip_ids),
            )
            .group_by(DisruptionEvent.trip_id)
        ).subquery()

        stmt = select(DisruptionEvent).join(
            mx,
            (DisruptionEvent.trip_id == mx.c.tid)
            & (DisruptionEvent.created_at == mx.c.mx_at)
            & (DisruptionEvent.user_id == user_id),
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        out: dict[str, DisruptionEvent | None] = {tid: None for tid in trip_ids}
        for ev in rows:
            out[ev.trip_id] = ev
        return out
