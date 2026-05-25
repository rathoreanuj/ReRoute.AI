"""Resolve airport/city coordinates for weather enrichment."""

from __future__ import annotations

from typing import Any

import httpx  # type: ignore[import-not-found]

from integrations.http_timeout import integration_timeout

# Fast-path exact airport coordinates (known high-traffic set).
IATA_COORDS: dict[str, tuple[float, float]] = {
    "ATL": (33.6407, -84.4277),
    "LAX": (33.9425, -118.4081),
    "ORD": (41.9742, -87.9073),
    "JFK": (40.6413, -73.7781),
    "EWR": (40.6895, -74.1745),
    "SFO": (37.6213, -122.3790),
    "SEA": (47.4502, -122.3088),
    "LHR": (51.4700, -0.4543),
    "CDG": (49.0097, 2.5479),
    "DXB": (25.2532, 55.3657),
    "DOH": (25.2731, 51.6080),
    "BOM": (19.0896, 72.8656),
    "DEL": (28.5562, 77.1000),
    "BLR": (13.1986, 77.7066),
    "HYD": (17.2403, 78.4294),
    "MAA": (12.9941, 80.1709),
    "CCU": (22.6547, 88.4467),
}

# Covers all airport options currently exposed in frontend AIRPORTS list.
IATA_TO_CITY: dict[str, str] = {
    "ATL": "Atlanta",
    "LAX": "Los Angeles",
    "ORD": "Chicago O'Hare",
    "DFW": "Dallas/Fort Worth",
    "DEN": "Denver",
    "JFK": "New York JFK",
    "SFO": "San Francisco",
    "SEA": "Seattle",
    "LAS": "Las Vegas",
    "MCO": "Orlando",
    "EWR": "Newark",
    "CLT": "Charlotte",
    "PHX": "Phoenix",
    "IAH": "Houston",
    "MIA": "Miami",
    "BOS": "Boston",
    "MSP": "Minneapolis",
    "DTW": "Detroit",
    "PHL": "Philadelphia",
    "LGA": "New York LaGuardia",
    "BWI": "Baltimore",
    "SLC": "Salt Lake City",
    "IAD": "Washington Dulles",
    "DCA": "Washington Reagan",
    "SAN": "San Diego",
    "PDX": "Portland OR",
    "STL": "St. Louis",
    "BNA": "Nashville",
    "AUS": "Austin",
    "TPA": "Tampa",
    "HNL": "Honolulu",
    "YVR": "Vancouver",
    "YYZ": "Toronto Pearson",
    "YUL": "Montreal",
    "YYC": "Calgary",
    "LHR": "London Heathrow",
    "LGW": "London Gatwick",
    "MAN": "Manchester",
    "EDI": "Edinburgh",
    "DUB": "Dublin",
    "CDG": "Paris CDG",
    "ORY": "Paris Orly",
    "AMS": "Amsterdam",
    "FRA": "Frankfurt",
    "MUC": "Munich",
    "ZRH": "Zurich",
    "VIE": "Vienna",
    "MAD": "Madrid",
    "BCN": "Barcelona",
    "LIS": "Lisbon",
    "FCO": "Rome Fiumicino",
    "MXP": "Milan Malpensa",
    "ATH": "Athens",
    "IST": "Istanbul",
    "DXB": "Dubai",
    "DOH": "Doha",
    "SIN": "Singapore",
    "HKG": "Hong Kong",
    "NRT": "Tokyo Narita",
    "HND": "Tokyo Haneda",
    "ICN": "Seoul",
    "PEK": "Beijing",
    "PVG": "Shanghai Pudong",
    "BOM": "Mumbai",
    "DEL": "New Delhi",
    "BLR": "Bengaluru",
    "HYD": "Hyderabad",
    "MAA": "Chennai",
    "CCU": "Kolkata",
    "COK": "Kochi",
    "SYD": "Sydney",
    "MEL": "Melbourne",
    "BNE": "Brisbane",
    "AKL": "Auckland",
    "GRU": "São Paulo",
    "GIG": "Rio de Janeiro",
    "MEX": "Mexico City",
    "CUN": "Cancún",
    "PTY": "Panama City",
    "BOG": "Bogotá",
    "LIM": "Lima",
    "SCL": "Santiago",
    "EZE": "Buenos Aires",
    "JNB": "Johannesburg",
    "CPT": "Cape Town",
    "CAI": "Cairo",
    "ADD": "Addis Ababa",
    "NBO": "Nairobi",
}


async def resolve_coords(query: str) -> tuple[float, float] | None:
    """Resolve by IATA first, then Open-Meteo geocoding by name."""
    q = query.strip()
    if not q:
        return None
    iata = q.upper()
    if iata in IATA_COORDS:
        return IATA_COORDS[iata]
    city_hint = IATA_TO_CITY.get(iata)
    if city_hint:
        q = city_hint

    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": q, "count": 1, "language": "en", "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=integration_timeout()) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            payload: dict[str, Any] = r.json()
    except Exception:
        return None
    rows = payload.get("results") or []
    if not rows:
        return None
    first = rows[0] if isinstance(rows[0], dict) else {}
    lat = first.get("latitude")
    lon = first.get("longitude")
    try:
        if lat is None or lon is None:
            return None
        return (float(lat), float(lon))
    except Exception:
        return None
