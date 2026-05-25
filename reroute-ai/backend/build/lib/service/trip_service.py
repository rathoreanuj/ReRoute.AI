"""Business logic: trips (DB snapshot for agent)."""

from __future__ import annotations

import copy
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from dao.itinerary_segment_dao import ItinerarySegmentDAO
from dao.leg_dao import LegDAO
from dao.trip_dao import TripDAO
from integrations.location_resolver import resolve_coords
from model.user_model import User
from schema.itinerary_schemas import ItinerarySegmentPublic, TripDetailPublic, TripLegPublic
from schema.trip_schemas import TripCreateRequest, TripPublic, TripUpdateRequest
from service.trip_derivative_sync import replace_derivatives_from_snapshot

logger = logging.getLogger(__name__)


def _validate_snapshot_shape(snapshot: dict) -> None:
    legs = snapshot.get("legs")
    if not isinstance(legs, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="snapshot.legs must be an object",
        )
    primary = legs.get("primary_flight")
    if not isinstance(primary, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="snapshot.legs.primary_flight is required",
        )
    for key in ("flight_number", "date", "origin", "destination"):
        if not primary.get(key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"snapshot.legs.primary_flight.{key} is required",
            )


def _hydrate_user_block(snapshot: dict, user: User) -> None:
    snap_user = snapshot.get("user")
    if not isinstance(snap_user, dict) or not snap_user.get("email"):
        snapshot["user"] = {
            "email": user.email,
            "full_name": user.full_name or user.email,
        }


async def _auto_fill_weather_coords(snapshot: dict) -> None:
    """Best-effort weather destination/origin coords from IATA/city fields."""
    legs = snapshot.get("legs")
    if not isinstance(legs, dict):
        return
    primary = legs.get("primary_flight")
    if not isinstance(primary, dict):
        return

    wx = legs.get("weather")
    if not isinstance(wx, dict):
        wx = {}
        legs["weather"] = wx

    if wx.get("destination_lat") is None or wx.get("destination_lon") is None:
        dest = primary.get("destination")
        if isinstance(dest, str) and dest.strip():
            coords = await resolve_coords(dest)
            if coords:
                wx["destination_lat"], wx["destination_lon"] = coords

    if wx.get("origin_lat") is None or wx.get("origin_lon") is None:
        origin = primary.get("origin")
        if isinstance(origin, str) and origin.strip():
            coords = await resolve_coords(origin)
            if coords:
                wx["origin_lat"], wx["origin_lon"] = coords


async def create_trip(
    *,
    user: User,
    payload: TripCreateRequest,
    session: AsyncSession,
) -> TripPublic:
    _validate_snapshot_shape(payload.snapshot)
    snapshot = copy.deepcopy(payload.snapshot)
    _hydrate_user_block(snapshot, user)
    await _auto_fill_weather_coords(snapshot)
    trip_id = str(uuid.uuid4())
    snapshot["trip_id"] = trip_id

    dao = TripDAO(session)
    trip = await dao.create(
        trip_id=trip_id,
        user_id=user.id,
        title=payload.title,
        snapshot=snapshot,
        itinerary_revision=0,
    )
    await replace_derivatives_from_snapshot(session=session, trip_id=trip.id, snapshot=snapshot)
    await session.commit()
    await session.refresh(trip)
    logger.info("trip_created", extra={"trip_id": trip.id, "user_id": user.id})
    return TripPublic.model_validate(trip)


async def list_trips(*, user_id: str, session: AsyncSession) -> list[TripPublic]:
    dao = TripDAO(session)
    rows = await dao.list_for_user(user_id=user_id)
    return [TripPublic.model_validate(t) for t in rows]


async def get_trip(*, user_id: str, trip_id: str, session: AsyncSession) -> TripPublic:
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return TripPublic.model_validate(trip)


async def get_trip_detail(*, user_id: str, trip_id: str, session: AsyncSession) -> TripDetailPublic:
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    legs = await LegDAO(session).list_for_trip(trip_id=trip_id)
    segments = await ItinerarySegmentDAO(session).list_for_trip(trip_id=trip_id)
    return TripDetailPublic(
        trip=TripPublic.model_validate(trip),
        legs=[TripLegPublic.model_validate(x) for x in legs],
        segments=[ItinerarySegmentPublic.model_validate(x) for x in segments],
    )


async def merge_applied_rebooking_to_snapshot(
    *,
    user_id: str,
    trip_id: str,
    session: AsyncSession,
    selected_offer_id: str,
    duffel_order_id: str | None,
    arrival_time: str | None,
    commit: bool = True,
) -> None:
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        return
    snap = copy.deepcopy(trip.snapshot)
    snap["applied_rebooking"] = {
        "selected_offer_id": selected_offer_id,
        "duffel_order_id": duffel_order_id,
        "arrival_time": arrival_time,
    }
    await dao.update(trip, snapshot=snap)
    await replace_derivatives_from_snapshot(session=session, trip_id=trip_id, snapshot=snap)
    if commit:
        await session.commit()
    logger.info("trip_snapshot_applied_rebooking", extra={"trip_id": trip_id, "user_id": user_id})


async def get_snapshot_for_agent(*, user_id: str, trip_id: str, session: AsyncSession) -> dict:
    """Deep copy of snapshot for agent tools (read-only isolation from ORM)."""
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return copy.deepcopy(trip.snapshot)


async def update_trip(
    *,
    user_id: str,
    trip_id: str,
    payload: TripUpdateRequest,
    session: AsyncSession,
) -> TripPublic:
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")

    updates: dict = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.snapshot is not None:
        _validate_snapshot_shape(payload.snapshot)
        snap = copy.deepcopy(payload.snapshot)
        snap["trip_id"] = trip_id
        await _auto_fill_weather_coords(snap)
        updates["snapshot"] = snap
    if payload.itinerary_revision is not None:
        updates["itinerary_revision"] = payload.itinerary_revision
    if updates:
        await dao.update(trip, **updates)
    if payload.snapshot is not None:
        await replace_derivatives_from_snapshot(session=session, trip_id=trip_id, snapshot=trip.snapshot)

    await session.commit()
    await session.refresh(trip)
    logger.info("trip_updated", extra={"trip_id": trip.id, "user_id": user_id})
    return TripPublic.model_validate(trip)


async def delete_trip(*, user_id: str, trip_id: str, session: AsyncSession) -> None:
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    await dao.delete(trip)
    await session.commit()
    logger.info("trip_deleted", extra={"trip_id": trip_id, "user_id": user_id})


async def bump_itinerary_revision(
    *,
    user_id: str,
    trip_id: str,
    session: AsyncSession,
    commit: bool = True,
) -> int | None:
    dao = TripDAO(session)
    trip = await dao.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
    if not trip:
        return None
    await dao.bump_itinerary_revision(trip)
    new_rev = int(trip.itinerary_revision)
    if commit:
        await session.commit()
    return new_rev
