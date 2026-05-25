"""Rebuild trip_legs + itinerary_segments from trip.snapshot (JSON remains canonical for the agent)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from dao.itinerary_segment_dao import ItinerarySegmentDAO
from dao.leg_dao import LegDAO

_SEGMENT_SPECS: list[tuple[str, str, int]] = [
    ("connection", "connection", 0),
    ("hotel", "hotel", 1),
    ("meeting", "meeting", 2),
    ("weather", "weather", 3),
]


async def replace_derivatives_from_snapshot(
    *,
    session: AsyncSession,
    trip_id: str,
    snapshot: dict,
) -> None:
    leg_dao = LegDAO(session)
    seg_dao = ItinerarySegmentDAO(session)
    await leg_dao.delete_all_for_trip(trip_id=trip_id)
    await seg_dao.delete_all_for_trip(trip_id=trip_id)

    legs = snapshot.get("legs")
    if not isinstance(legs, dict):
        return

    pf = legs.get("primary_flight")
    if isinstance(pf, dict) and pf.get("flight_number") and pf.get("origin") and pf.get("destination"):
        known = {"flight_number", "date", "origin", "destination", "scheduled_departure_date"}
        extra = {k: v for k, v in pf.items() if k not in known} or None
        await leg_dao.create(
            trip_id=trip_id,
            segment_order=0,
            mode="flight",
            origin_code=str(pf["origin"]),
            destination_code=str(pf["destination"]),
            flight_number=str(pf.get("flight_number") or "") or None,
            travel_date=str(pf.get("date") or "") or None,
            extra=extra,
        )

    for _key, kind, order in _SEGMENT_SPECS:
        block = legs.get(kind)
        if isinstance(block, dict) and block:
            await seg_dao.create(
                trip_id=trip_id,
                segment_order=order,
                kind=kind,
                payload=block,
            )
