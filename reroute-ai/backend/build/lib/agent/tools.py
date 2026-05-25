"""Agent tools: call integrations; return JSON for agent state (propose/confirm).

All tools are side-effect-free (except outbound API calls).
"""

from __future__ import annotations

import logging
from typing import Any

from integrations.flight_search import search_rebooking_offers_with_duffel
from integrations.flight_status import get_flight_status_aviationstack
from integrations.weather import fetch_openmeteo_hourly

logger = logging.getLogger(__name__)


async def fetch_flight_status(
    *,
    flight_number: str,
    date: str,
    simulate_disruption: str | None = None,
) -> dict[str, Any]:
    """Normalized flight disruption classification."""
    logger.info(
        "fetch_flight_status",
        extra={"flight_number": flight_number, "date": date, "simulate": simulate_disruption},
    )
    return await get_flight_status_aviationstack(
        flight_number=flight_number,
        date=date,
        simulate_disruption=simulate_disruption,
    )


async def fetch_weather_signals(*, latitude: float, longitude: float) -> dict[str, Any]:
    """Open-Meteo hourly weather signals used for radar risk."""
    payload = await fetch_openmeteo_hourly(latitude=latitude, longitude=longitude)
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    def _latest(key: str):
        vals = hourly.get(key, [])
        if isinstance(vals, list) and vals:
            return vals[-1]
        return None
    return {
        "source": "open-meteo",
        # Keep it compact; we just need latest hour values.
        "latest": {
            "time": _latest("time"),
            "precipitation_probability": _latest("precipitation_probability"),
            "weather_code": _latest("weather_code"),
            "wind_speed_10m": _latest("wind_speed_10m"),
            "temperature_2m": _latest("temperature_2m"),
        },
    }


async def search_alternatives(
    *,
    trip_context: dict[str, Any],
    simulate_disruption: str | None = None,
) -> dict[str, Any]:
    """Duffel offer search for flight alternatives."""
    result = await search_rebooking_offers_with_duffel(
        trip_context=trip_context,
        simulate_disruption=simulate_disruption,
    )
    return {
        "source": "duffel",
        "orq": result,
    }


TOOL_REGISTRY = {
    "fetch_flight_status": fetch_flight_status,
    "fetch_weather_signals": fetch_weather_signals,
    "search_alternatives": search_alternatives,
}
