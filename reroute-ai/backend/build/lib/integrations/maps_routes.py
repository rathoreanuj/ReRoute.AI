"""Driving directions summary via OpenRouteService (optional API key)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import get_settings
from integrations.http_timeout import integration_timeout

logger = logging.getLogger(__name__)

ORS_DIRECTIONS = "https://api.openrouteservice.org/v2/directions"


async def fetch_directions_summary(
    *,
    origin_lon: float,
    origin_lat: float,
    dest_lon: float,
    dest_lat: float,
    profile: str = "driving-car",
) -> dict[str, Any]:
    """
    Return distance (m), duration (s) if ORS is configured; fail-closed otherwise.
    Coordinates are WGS84 (lon, lat).
    """
    settings = get_settings()
    if not settings.OPENROUTESERVICE_API_KEY:
        return {
            "source": "openrouteservice_disabled",
            "distance_m": None,
            "duration_s": None,
            "profile": profile,
        }

    url = f"{ORS_DIRECTIONS}/{profile}"
    body = {"coordinates": [[origin_lon, origin_lat], [dest_lon, dest_lat]]}
    headers = {
        "Authorization": settings.OPENROUTESERVICE_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=integration_timeout()) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("ors_http_error", extra={"status": e.response.status_code})
        return {
            "source": "openrouteservice_error",
            "distance_m": None,
            "duration_s": None,
            "reason": f"http_{e.response.status_code}",
        }
    except Exception as e:
        logger.warning("ors_request_failed", extra={"error": type(e).__name__})
        return {
            "source": "openrouteservice_error",
            "distance_m": None,
            "duration_s": None,
            "reason": type(e).__name__,
        }

    routes = data.get("routes") or []
    if not routes:
        return {
            "source": "openrouteservice",
            "distance_m": None,
            "duration_s": None,
            "reason": "no_routes",
        }
    summary = routes[0].get("summary") or {}
    return {
        "source": "openrouteservice",
        "distance_m": summary.get("distance"),
        "duration_s": summary.get("duration"),
        "profile": profile,
    }
