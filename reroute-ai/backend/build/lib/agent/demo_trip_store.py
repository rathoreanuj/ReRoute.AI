"""Demo trip snapshot template for tests and seeding POST /trips bodies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Passenger:
    title: str
    given_name: str
    family_name: str
    gender: str
    phone_number: str
    email: str
    born_on: str  # YYYY-MM-DD


def _now_utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S%z")


def demo_snapshot_for_api() -> dict[str, Any]:
    """Body for POST /trips: same shape as a loaded snapshot but without trip_id (server sets it)."""
    data = load_trip_snapshot("demo-seed")
    return {k: v for k, v in data.items() if k != "trip_id"}


def load_trip_snapshot(trip_id: str) -> dict[str, Any]:
    """
    Minimal demo snapshot compatible with the agent tools.
    We keep it deterministic so Duffel test-mode booking works reliably.
    """
    # Duffel test mode examples use specific dates; keep them stable so offers
    # appear reliably in the hackathon demo environment.
    base_departure_date = "2026-04-01"

    passengers = [
        Passenger(
            title="mr",
            given_name="Tony",
            family_name="Stark",
            gender="m",
            phone_number="+442080160508",
            email="tony@example.com",
            born_on="1980-07-24",
        ),
        Passenger(
            title="mrs",
            given_name="Pepper",
            family_name="Potts",
            gender="f",
            phone_number="+442080160509",
            email="pepper@example.com",
            born_on="1983-11-02",
        ),
    ]

    return {
        "trip_id": trip_id,
        "user": {"email": "tony@example.com", "full_name": "Tony Stark"},
        "passengers": [p.__dict__ for p in passengers],
        "preferences": {"cabin_class": "economy", "budget_band": "mid"},
        "legs": {
            "primary_flight": {
                "flight_number": "2117",
                "date": base_departure_date,
                "origin": "NYC",
                "destination": "ATL",
                "scheduled_departure_date": base_departure_date,
            },
            # Connection + meetings are used for cascade preview. Keep placeholders.
            "connection": {
                "departure_after_arrival_minutes": 90,
            },
            "hotel": {"check_in_buffer_minutes": 60},
            "meeting": {"scheduled_time_utc": _now_utc_iso()},
            # Coordinates needed for Open-Meteo. Use generic coords for NYC/ATL.
            "weather": {
                "origin_lat": 40.7128,
                "origin_lon": -74.0060,
                "destination_lat": 33.7490,
                "destination_lon": -84.3880,
            },
        },
    }

