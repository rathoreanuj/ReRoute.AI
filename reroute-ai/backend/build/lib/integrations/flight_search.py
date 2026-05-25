"""Flight search client — Duffel-based offer requests (test-mode compatible)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from integrations.duffel_client import search_flight_offers_with_polling


def _build_duffel_offer_request_data(*, slices: list[dict[str, Any]], passengers: list[dict[str, Any]], cabin_class: str) -> dict:
    return {
        "slices": slices,
        "passengers": passengers,
        "cabin_class": cabin_class,
    }


def _with_meta(result: dict[str, Any], *, requested_date: str, selected_date: str, tried_dates: list[str]) -> dict[str, Any]:
    out = dict(result)
    out["_reroute_meta"] = {
        "requested_departure_date": requested_date,
        "selected_departure_date": selected_date,
        "tried_departure_dates": tried_dates,
        "date_shifted": selected_date != requested_date,
    }
    return out


async def search_rebooking_offers_with_duffel(
    *,
    trip_context: dict[str, Any],
    simulate_disruption: str | None = None,
) -> dict[str, Any]:
    """
    Uses Duffel offer_requests and returns the ORQ results JSON including:
      - data.offers (list)
      - data.passengers (list with Duffel passenger IDs)
    """
    primary = trip_context["legs"]["primary_flight"]
    cabin_class = trip_context.get("preferences", {}).get("cabin_class", "economy")

    # Duffel slices accept origin/destination IATA or city codes and departure_date.
    slices = [
        {
            "origin": primary["origin"],
            "destination": primary["destination"],
            "departure_date": primary["date"],
        }
    ]

    def _age_on_departure(born_on: str, departure_date: str) -> int | None:
        try:
            b = datetime.strptime(born_on, "%Y-%m-%d").date()
            d = datetime.strptime(departure_date, "%Y-%m-%d").date()
            years = d.year - b.year - ((d.month, d.day) < (b.month, b.day))
            return max(0, years)
        except Exception:
            return None

    def _duffel_passenger_from_snapshot(p: dict[str, Any], departure_date: str) -> dict[str, Any]:
        born_on = p.get("born_on")
        born = born_on.strip() if isinstance(born_on, str) and born_on.strip() else "1990-01-01"
        age = _age_on_departure(str(born), departure_date)
        # If DOB is in the future or clearly wrong, treat as adult
        if age is None or age < 0:
            return {"type": "adult", "born_on": "1990-01-01"}
        if age < 2:
            # Infants without an adult cause empty searches — fall back to adult
            return {"type": "adult", "born_on": born}
        if age < 12:
            return {"type": "child", "age": age, "born_on": born}
        return {"type": "adult", "born_on": born}

    # Keep search passengers aligned with snapshot traveler ages when available.
    snap_passengers = [x for x in trip_context.get("passengers", []) if isinstance(x, dict)]
    passengers = [
        _duffel_passenger_from_snapshot(p, primary["date"]) for p in snap_passengers
    ] or [{"type": "adult"}]

    offer_request_data = _build_duffel_offer_request_data(
        slices=slices,
        passengers=passengers,
        cabin_class=cabin_class,
    )
    # NOTE: simulate_disruption is unused for search; it influences ranking.
    requested_date = primary["date"]
    tried_dates: list[str] = [requested_date]
    first = await search_flight_offers_with_polling(offer_request_data=offer_request_data)
    offers = ((first.get("data") or {}).get("offers") or []) if isinstance(first, dict) else []
    if offers:
        return _with_meta(first, requested_date=requested_date, selected_date=requested_date, tried_dates=tried_dates)

    # Retry nearby dates to avoid empty same-day inventory in test mode.
    try:
        base_date = datetime.strptime(primary["date"], "%Y-%m-%d").date()
    except Exception:
        return first
    for delta in (1, 2):
        alt_date = (base_date + timedelta(days=delta)).isoformat()
        alt_slices = [
            {
                "origin": primary["origin"],
                "destination": primary["destination"],
                "departure_date": alt_date,
            }
        ]
        alt_orq = _build_duffel_offer_request_data(
            slices=alt_slices,
            passengers=passengers,
            cabin_class=cabin_class,
        )
        out = await search_flight_offers_with_polling(offer_request_data=alt_orq)
        tried_dates.append(alt_date)
        alt_offers = ((out.get("data") or {}).get("offers") or []) if isinstance(out, dict) else []
        if alt_offers:
            return _with_meta(out, requested_date=requested_date, selected_date=alt_date, tried_dates=tried_dates)
    return _with_meta(first, requested_date=requested_date, selected_date=requested_date, tried_dates=tried_dates)

