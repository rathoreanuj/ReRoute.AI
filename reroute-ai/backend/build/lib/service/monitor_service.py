"""Aggregate monitor status for the dashboard (DB-backed, no external polling in v1)."""

from __future__ import annotations

import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from dao.disruption_event_dao import DisruptionEventDAO
from dao.proposal_dao import ProposalDAO
from dao.trip_dao import TripDAO
from schema.monitor_schemas import MonitorStatusResponse, MonitorTripSummary

logger = logging.getLogger(__name__)

_MAX_TRIPS_IN_STATUS = 50


async def build_status(*, session: AsyncSession, user_id: str) -> MonitorStatusResponse:
    trip_dao = TripDAO(session)
    prop_dao = ProposalDAO(session)
    ev_dao = DisruptionEventDAO(session)

    total_trip_count = await trip_dao.count_for_user(user_id=user_id)
    trips = await trip_dao.list_for_user(user_id=user_id, limit=_MAX_TRIPS_IN_STATUS)
    trip_ids = [t.id for t in trips]
    total_pending = await prop_dao.count_pending_for_user(user_id=user_id)
    pending_by_trip = await prop_dao.count_pending_grouped_by_trips(user_id=user_id, trip_ids=trip_ids)
    latest_by_trip = await ev_dao.latest_per_trip_for_user(user_id=user_id, trip_ids=trip_ids)

    summaries: list[MonitorTripSummary] = []
    for t in trips:
        pending_trip = pending_by_trip.get(t.id, 0)
        latest = latest_by_trip.get(t.id)
        summaries.append(
            MonitorTripSummary(
                trip_id=t.id,
                title=t.title,
                itinerary_revision=int(t.itinerary_revision),
                pending_proposal_count=pending_trip,
                last_disruption_kind=latest.kind if latest else None,
                last_disruption_at=latest.created_at if latest else None,
            )
        )

    now = datetime.datetime.now(datetime.UTC)
    logger.info(
        "monitor_status",
        extra={"user_id": user_id, "trip_count": total_trip_count, "trips_shown": len(summaries)},
    )
    return MonitorStatusResponse(
        generated_at=now,
        trip_count=total_trip_count,
        trips_shown=len(summaries),
        total_pending_proposals=total_pending,
        trips=summaries,
    )
