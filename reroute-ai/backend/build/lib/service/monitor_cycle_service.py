"""Periodic monitor: flight + weather checks per trip; disruption events on change."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.tools import fetch_flight_status, fetch_weather_signals
from config import get_settings
from dao.disruption_event_dao import DisruptionEventDAO
from dao.trip_dao import TripDAO
from integrations.location_resolver import resolve_coords

logger = logging.getLogger(__name__)


def _safe_int(v: object) -> int | None:
    try:
        return int(v) if v is not None else None  # type: ignore[arg-type]
    except Exception:
        return None


def _enqueue_autonomous_handler(
    *,
    user_id: str,
    trip_id: str,
    disruption_type: str,
    flight_status: dict[str, Any],
) -> None:
    """Enqueue the autonomous disruption handler as a Celery task."""
    try:
        from worker.tasks import run_autonomous_disruption_task
        run_autonomous_disruption_task.delay(
            user_id=user_id,
            trip_id=trip_id,
            disruption_type=disruption_type,
            flight_status=flight_status,
        )
        logger.info(
            "autonomous_handler_enqueued",
            extra={"trip_id": trip_id, "disruption_type": disruption_type},
        )
    except Exception:
        logger.exception("autonomous_handler_enqueue_failed")


_SEVERE_WEATHER_CODES = {65, 75, 82, 85, 86, 95, 96, 99}


def _weather_severity_bucket(code: Any) -> str:
    """Map weather code to a coarse bucket so minor changes don't trigger alerts."""
    try:
        c = int(code) if code is not None else -1
    except (ValueError, TypeError):
        return "unknown"
    if c in _SEVERE_WEATHER_CODES:
        return "severe"
    if c in {61, 63, 71, 73, 80, 81}:
        return "moderate"
    if c in {45, 48, 51, 53, 55}:
        return "light"
    if c in {0, 1, 2, 3}:
        return "clear"
    return "other"


def _weather_codes_latest(weather: dict[str, Any]) -> str:
    # New shape: weather.origin_latest / weather.destination_latest with scalar fields.
    # Only track severity buckets (not raw codes) to avoid false alerts on minor shifts.
    origin = weather.get("origin_latest") if isinstance(weather.get("origin_latest"), dict) else {}
    destination = weather.get("destination_latest") if isinstance(weather.get("destination_latest"), dict) else {}
    if origin or destination:
        signature = {
            "origin_severity": _weather_severity_bucket(origin.get("weather_code")),
            "destination_severity": _weather_severity_bucket(destination.get("weather_code")),
        }
        return json.dumps(signature, separators=(",", ":"), sort_keys=True)

    # Backward compatibility with older latest.weather_code list/scalar payloads.
    latest = weather.get("latest") or {}
    if isinstance(latest, dict):
        codes = latest.get("weather_code")
        if isinstance(codes, list):
            return json.dumps(codes[-3:], separators=(",", ":"))
        if codes is not None:
            return str(codes)
    return ""


def _scan_signature(flight_status: dict[str, Any], weather: dict[str, Any]) -> str:
    return "|".join(
        [
            str(flight_status.get("status") or ""),
            str(flight_status.get("delay_minutes")),
            str(flight_status.get("source") or ""),
            _weather_codes_latest(weather),
        ]
    )


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def run_monitor_cycle(*, session: AsyncSession) -> dict[str, int]:
    """
    Scan trips with primary_flight in snapshot; throttle per trip; record monitor_scan;
    emit monitor_alert when observation signature changes vs previous scan.
    """
    s = get_settings()
    min_gap = timedelta(minutes=max(1, s.monitor_min_trip_interval_minutes))
    batch = max(1, min(s.monitor_batch_size, 200))
    cap = max(1, min(s.monitor_max_trips_per_cycle, 2000))

    trip_dao = TripDAO(session)
    ev_dao = DisruptionEventDAO(session)

    scanned = 0
    skipped_throttle = 0
    skipped_no_flight = 0
    alerts = 0
    errors = 0
    offset = 0
    processed = 0

    while processed < cap:
        trips = await trip_dao.list_all(offset=offset, limit=batch)
        if not trips:
            break
        offset += len(trips)

        trip_ids = [t.id for t in trips]
        latest_scans = await ev_dao.latest_by_kind_for_trip_ids(trip_ids=trip_ids, kind="monitor_scan")

        for trip in trips:
            if processed >= cap:
                break
            processed += 1

            snap = trip.snapshot if isinstance(trip.snapshot, dict) else {}
            legs = snap.get("legs") if isinstance(snap.get("legs"), dict) else {}
            pf = legs.get("primary_flight")
            if not isinstance(pf, dict) or not pf.get("flight_number") or not pf.get("date"):
                skipped_no_flight += 1
                continue

            last_scan = latest_scans.get(trip.id)
            if last_scan and last_scan.created_at:
                age = datetime.now(UTC) - _naive_utc(last_scan.created_at)
                if age < min_gap:
                    skipped_throttle += 1
                    continue

            try:
                flight_status = await fetch_flight_status(
                    flight_number=str(pf["flight_number"]),
                    date=str(pf["date"]),
                    simulate_disruption=None,
                )
            except Exception:
                logger.exception("monitor_cycle_flight_status", extra={"trip_id": trip.id})
                errors += 1
                continue

            wx = legs.get("weather") if isinstance(legs.get("weather"), dict) else {}
            origin_iata = str(pf.get("origin") or "").upper()
            dest_iata = str(pf.get("destination") or "").upper()
            origin_city = str(snap.get("origin_city") or "")
            dest_city = str(snap.get("destination_city") or "")

            origin_lat = wx.get("origin_lat")
            origin_lon = wx.get("origin_lon")
            dest_lat = wx.get("destination_lat")
            dest_lon = wx.get("destination_lon")
            if origin_lat is None or origin_lon is None:
                coords = await resolve_coords(origin_iata or origin_city)
                if coords:
                    origin_lat = origin_lat if origin_lat is not None else coords[0]
                    origin_lon = origin_lon if origin_lon is not None else coords[1]
            if dest_lat is None or dest_lon is None:
                coords = await resolve_coords(dest_iata or dest_city)
                if coords:
                    dest_lat = dest_lat if dest_lat is not None else coords[0]
                    dest_lon = dest_lon if dest_lon is not None else coords[1]

            weather: dict[str, Any] = {"source": "monitor_cycle", "origin_latest": {}, "destination_latest": {}}
            if origin_lat is not None and origin_lon is not None:
                try:
                    ow = await fetch_weather_signals(latitude=float(origin_lat), longitude=float(origin_lon))
                    weather["origin_latest"] = ow.get("latest") if isinstance(ow.get("latest"), dict) else {}
                except Exception:
                    weather["origin_latest"] = {}
            if dest_lat is not None and dest_lon is not None:
                try:
                    dw = await fetch_weather_signals(latitude=float(dest_lat), longitude=float(dest_lon))
                    weather["destination_latest"] = dw.get("latest") if isinstance(dw.get("latest"), dict) else {}
                except Exception:
                    weather["destination_latest"] = {}

            signature = _scan_signature(flight_status, weather)
            prev_sig: str | None = None
            if last_scan and isinstance(last_scan.payload, dict):
                prev_sig = last_scan.payload.get("signature")

            disruption_type = str(flight_status.get("status") or "unknown")

            await ev_dao.create(
                trip_id=trip.id,
                user_id=trip.user_id,
                kind="monitor_scan",
                disruption_type=disruption_type,
                proposal_id=None,
                payload={
                    "signature": signature,
                    "flight_status": flight_status,
                    "weather": weather,
                    "source": "monitor_cycle",
                },
            )
            scanned += 1

            if prev_sig is not None and prev_sig != signature:
                origin_wx = weather.get("origin_latest", {})
                dest_wx = weather.get("destination_latest", {})
                origin_severe = int(origin_wx.get("weather_code") or 0) in _SEVERE_WEATHER_CODES
                dest_severe = int(dest_wx.get("weather_code") or 0) in _SEVERE_WEATHER_CODES
                await ev_dao.create(
                    trip_id=trip.id,
                    user_id=trip.user_id,
                    kind="monitor_alert",
                    disruption_type=disruption_type,
                    proposal_id=None,
                    payload={
                        "previous_signature": prev_sig,
                        "current_signature": signature,
                        "flight_status": flight_status,
                        "weather": weather,
                        "severe_weather_origin": origin_severe,
                        "severe_weather_destination": dest_severe,
                        "source": "monitor_cycle",
                    },
                )
                alerts += 1

                # ── AUTONOMOUS AGENT TRIGGER ──
                # Only trigger for real disruptions (not minor delays or weather-only changes)
                is_real_disruption = disruption_type in ("cancelled", "diverted") or (
                    disruption_type == "delayed" and (_safe_int(flight_status.get("delay_minutes")) or 0) >= 60
                )
                if is_real_disruption:
                    try:
                        _enqueue_autonomous_handler(
                            user_id=trip.user_id,
                            trip_id=trip.id,
                            disruption_type=disruption_type,
                            flight_status=flight_status,
                        )
                    except Exception:
                        logger.exception("autonomous_handler_enqueue_failed", extra={"trip_id": trip.id})

        await session.commit()

    logger.info(
        "monitor_cycle_done",
        extra={
            "scanned": scanned,
            "alerts": alerts,
            "skipped_throttle": skipped_throttle,
            "skipped_no_flight": skipped_no_flight,
            "errors": errors,
            "processed": processed,
        },
    )
    return {
        "scanned": scanned,
        "alerts": alerts,
        "skipped_throttle": skipped_throttle,
        "skipped_no_flight": skipped_no_flight,
        "errors": errors,
        "processed": processed,
    }
