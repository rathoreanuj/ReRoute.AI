"""Persist and load agent proposals (DB)."""

from __future__ import annotations

import copy

from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from dao.disruption_event_dao import DisruptionEventDAO
from dao.proposal_dao import ProposalDAO
from model.proposal_model import RebookingProposal


async def get_proposal_row(
    *,
    session: AsyncSession,
    proposal_id: str,
    user_id: str,
) -> RebookingProposal | None:
    return await ProposalDAO(session).get_by_id_for_user(proposal_id=proposal_id, user_id=user_id)


async def try_claim_confirm(
    *,
    session: AsyncSession,
    proposal_id: str,
    user_id: str,
) -> bool:
    return await ProposalDAO(session).try_claim_pending_for_confirm(proposal_id=proposal_id, user_id=user_id)


async def release_confirm_claim(
    *,
    session: AsyncSession,
    proposal_id: str,
    user_id: str,
) -> None:
    await ProposalDAO(session).release_applying_confirm(proposal_id=proposal_id, user_id=user_id)


async def persist_new_proposal(
    *,
    session: AsyncSession,
    proposal_id: str,
    trip_id: str,
    user_id: str,
    context: dict,
    disruption_type: str,
    tool_trace_summary: list[str],
    ranked_option_ids: list[str],
    commit: bool = True,
) -> None:
    pdao = ProposalDAO(session)
    edao = DisruptionEventDAO(session)
    await pdao.create(proposal_id=proposal_id, trip_id=trip_id, user_id=user_id, context=context)
    await edao.create(
        trip_id=trip_id,
        user_id=user_id,
        kind="agent_propose",
        disruption_type=disruption_type,
        proposal_id=proposal_id,
        payload={
            "tool_trace_summary": tool_trace_summary,
            "ranked_option_ids": ranked_option_ids,
        },
    )
    if commit:
        await session.commit()


async def fetch_proposal_context(
    *,
    session: AsyncSession,
    proposal_id: str,
    user_id: str,
) -> dict | None:
    row = await ProposalDAO(session).get_by_id_for_user(proposal_id=proposal_id, user_id=user_id)
    if not row:
        return None
    return copy.deepcopy(row.context)


async def mark_proposal_applied(
    *,
    session: AsyncSession,
    proposal_id: str,
    user_id: str,
    disruption_type: str | None,
    selected_offer_id: str,
    duffel_order_id: str | None,
    commit: bool = True,
) -> bool:
    pdao = ProposalDAO(session)
    edao = DisruptionEventDAO(session)
    row = await pdao.get_by_id_for_user(proposal_id=proposal_id, user_id=user_id)
    if not row:
        return False
    if row.status != "applying":
        return False
    await pdao.mark_applied(
        row,
        selected_offer_id=selected_offer_id,
        duffel_order_id=duffel_order_id,
    )
    await edao.create(
        trip_id=row.trip_id,
        user_id=user_id,
        kind="agent_confirm",
        disruption_type=disruption_type,
        proposal_id=proposal_id,
        payload={
            "selected_offer_id": selected_offer_id,
            "duffel_order_id": duffel_order_id,
        },
    )
    if commit:
        await session.commit()
    return True


async def release_stale_applying(
    *,
    session: AsyncSession,
    older_than_minutes: int | None = None,
) -> int:
    minutes = older_than_minutes if older_than_minutes is not None else get_settings().stale_applying_minutes
    return await ProposalDAO(session).revert_stale_applying(older_than_minutes=minutes)
