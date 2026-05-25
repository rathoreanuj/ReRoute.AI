"""Data access: rebooking proposals."""

from __future__ import annotations

import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dao.base_dao import BaseDAO
from model.proposal_model import RebookingProposal


class ProposalDAO(BaseDAO):
    def __init__(self, session: AsyncSession):
        super().__init__(RebookingProposal, session)

    async def get_by_id_for_user(self, *, proposal_id: str, user_id: str) -> RebookingProposal | None:
        result = await self.session.execute(
            select(RebookingProposal).where(
                RebookingProposal.id == proposal_id,
                RebookingProposal.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        proposal_id: str,
        trip_id: str,
        user_id: str,
        context: dict,
        status: str = "pending",
    ) -> RebookingProposal:
        row = RebookingProposal(
            id=proposal_id,
            trip_id=trip_id,
            user_id=user_id,
            status=status,
            context=context,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def count_pending_for_user(self, *, user_id: str) -> int:
        q = select(func.count()).select_from(RebookingProposal).where(
            RebookingProposal.user_id == user_id,
            RebookingProposal.status == "pending",
        )
        result = await self.session.execute(q)
        return int(result.scalar_one() or 0)

    async def count_pending_for_trip(self, *, trip_id: str, user_id: str) -> int:
        q = select(func.count()).select_from(RebookingProposal).where(
            RebookingProposal.trip_id == trip_id,
            RebookingProposal.user_id == user_id,
            RebookingProposal.status == "pending",
        )
        result = await self.session.execute(q)
        return int(result.scalar_one() or 0)

    async def count_pending_grouped_by_trips(
        self, *, user_id: str, trip_ids: list[str]
    ) -> dict[str, int]:
        if not trip_ids:
            return {}
        stmt = (
            select(RebookingProposal.trip_id, func.count())
            .where(
                RebookingProposal.user_id == user_id,
                RebookingProposal.trip_id.in_(trip_ids),
                RebookingProposal.status == "pending",
            )
            .group_by(RebookingProposal.trip_id)
        )
        result = await self.session.execute(stmt)
        counts = {tid: 0 for tid in trip_ids}
        for trip_id, cnt in result.all():
            counts[str(trip_id)] = int(cnt)
        return counts

    async def try_claim_pending_for_confirm(self, *, proposal_id: str, user_id: str) -> bool:
        """Atomically move pending -> applying so only one confirm proceeds to external booking."""
        stmt = (
            update(RebookingProposal)
            .where(
                RebookingProposal.id == proposal_id,
                RebookingProposal.user_id == user_id,
                RebookingProposal.status == "pending",
            )
            .values(status="applying")
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.rowcount == 1

    async def release_applying_confirm(self, *, proposal_id: str, user_id: str) -> None:
        stmt = (
            update(RebookingProposal)
            .where(
                RebookingProposal.id == proposal_id,
                RebookingProposal.user_id == user_id,
                RebookingProposal.status == "applying",
            )
            .values(status="pending")
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def revert_stale_applying(self, *, older_than_minutes: int) -> int:
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=older_than_minutes)
        stmt = (
            update(RebookingProposal)
            .where(
                RebookingProposal.status == "applying",
                RebookingProposal.updated_at < cutoff,
            )
            .values(status="pending")
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return int(res.rowcount or 0)

    async def mark_applied(
        self,
        row: RebookingProposal,
        *,
        selected_offer_id: str,
        duffel_order_id: str | None,
    ) -> RebookingProposal:
        row.status = "applied"
        row.selected_offer_id = selected_offer_id
        row.duffel_order_id = duffel_order_id
        await self.session.flush()
        await self.session.refresh(row)
        return row
