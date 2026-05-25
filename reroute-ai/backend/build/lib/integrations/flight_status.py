"""Flight status client (AviationStack) with demo fallbacks."""

from __future__ import annotations

from datetime import datetime

import httpx

from config import get_settings
from integrations.http_timeout import integration_timeout

AVIATIONSTACK_URL = "https://api.aviationstack.com/v1/flights"


def _simulate(simulate_disruption: str | None) -> dict:
    """
    Returns a normalized classification.
    simulate_disruption examples: delay|cancel|divert
    """
    if simulate_disruption == "cancel":
        return {
            "status": "cancelled",
            "delay_minutes": 0,
            "source": "simulation",
        }
    if simulate_disruption == "divert":
        return {
            "status": "diverted",
            "delay_minutes": 60,
            "source": "simulation",
        }
    # Default: delay
    if simulate_disruption in (None, "delay"):
        return {
            "status": "delayed",
            "delay_minutes": 180,
            "source": "simulation",
        }
    return {"status": "unknown", "delay_minutes": None, "source": "simulation"}


def _parse_iso_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    txt = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(txt)
    except Exception:
        return None


def _derive_delay_minutes(item: dict) -> int | None:
    # Prefer API explicit delay when present.
    raw_delay = item.get("delay") or item.get("delay_minutes")
    if raw_delay is not None:
        try:
            return max(0, int(raw_delay))
        except Exception:
            pass
    dep = item.get("departure") if isinstance(item.get("departure"), dict) else {}
    arr = item.get("arrival") if isinstance(item.get("arrival"), dict) else {}
    # Try departure scheduled vs estimated/actual first.
    dep_sched = _parse_iso_dt(dep.get("scheduled"))
    dep_est = _parse_iso_dt(dep.get("estimated")) or _parse_iso_dt(dep.get("actual"))
    if dep_sched and dep_est:
        mins = int((dep_est - dep_sched).total_seconds() // 60)
        return max(0, mins)
    # Fallback arrival scheduled vs estimated/actual.
    arr_sched = _parse_iso_dt(arr.get("scheduled"))
    arr_est = _parse_iso_dt(arr.get("estimated")) or _parse_iso_dt(arr.get("actual"))
    if arr_sched and arr_est:
        mins = int((arr_est - arr_sched).total_seconds() // 60)
        return max(0, mins)
    return None


async def get_flight_status_aviationstack(
    *,
    flight_number: str,
    date: str,
    simulate_disruption: str | None = None,
) -> dict:
    """
    Normalized output:
      - status: delayed|cancelled|diverted|unknown
      - delay_minutes: number or None
      - source: aviationstack|simulation|error
    """
    if simulate_disruption:
        return _simulate(simulate_disruption)

    settings = get_settings()
    if not settings.AVIATION_STACK_API_KEY:
        return {"status": "unknown", "delay_minutes": None, "source": "missing_api_key"}

    params = {
        "access_key": settings.AVIATION_STACK_API_KEY,
        "flight_iata": flight_number.replace(" ", "").upper(),
        "date": date,
    }

    payload: dict = {}
    err_source: str | None = None
    for _attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=integration_timeout()) as client:
                r = await client.get(AVIATIONSTACK_URL, params=params)
                r.raise_for_status()
                payload = r.json()
                err_source = None
                break
        except Exception as e:
            err_source = f"error:{type(e).__name__}"
            continue
    if err_source:
        return {"status": "unknown", "delay_minutes": None, "source": err_source}

    # Aviationstack responses contain `data` list under v1.
    data = payload.get("data") or []
    if not data:
        return {"status": "unknown", "delay_minutes": None, "source": "aviationstack_no_data"}

    # Heuristic: try common fields used for status.
    item = data[0]
    status_text = (
        str(item.get("flight_status") or item.get("status") or "").lower().strip()
    )
    delay_minutes = _derive_delay_minutes(item)

    if "cancel" in status_text:
        return {"status": "cancelled", "delay_minutes": 0, "source": "aviationstack"}
    if "divert" in status_text:
        return {"status": "diverted", "delay_minutes": delay_minutes, "source": "aviationstack"}
    if delay_minutes is not None and int(delay_minutes) >= 15:
        return {"status": "delayed", "delay_minutes": int(delay_minutes), "source": "aviationstack"}

    # If it's not a disruption, return its actual status (active, scheduled, etc) instead of unknown
    if status_text in ("active", "scheduled", "landed"):
        return {"status": status_text, "delay_minutes": delay_minutes or 0, "source": "aviationstack"}

    return {"status": status_text or "unknown", "delay_minutes": delay_minutes, "source": "aviationstack"}

