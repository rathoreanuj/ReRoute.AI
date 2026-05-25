"""Weather client (Open-Meteo)."""

from __future__ import annotations

import httpx

from integrations.http_timeout import integration_timeout


async def fetch_openmeteo_hourly(*, latitude: float, longitude: float) -> dict:
    """
    Fetch minimal hourly weather signals used for disruption risk:
    - precipitation_probability
    - weather_code
    - wind_speed_10m
    - temperature_2m
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "UTC",
        "hourly": ",".join(
            [
                "precipitation_probability",
                "weather_code",
                "wind_speed_10m",
                "temperature_2m",
            ]
        ),
    }
    async with httpx.AsyncClient(timeout=integration_timeout()) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

