from fastapi import APIRouter, Query, HTTPException
from datetime import date
from agent.tools import fetch_flight_status
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/flight-status")
async def public_flight_status(
    flight_number: str = Query(..., description="E.g., AA104 or UA 123"),
    check_date: str | None = Query(None, description="ISO Date string"),
):
    """Public flight status lookup — no auth required. Used by landing page search bar."""
    fn = flight_number.strip().upper().replace(" ", "")
    target_date = check_date if check_date else date.today().isoformat()

    try:
        status_info = await fetch_flight_status(flight_number=fn, date=target_date)
        status = status_info.get("status", "unknown")
        source = status_info.get("source", "unknown")

        # AviationStack returned no data for this flight/date — show as "scheduled" (not alarming "unknown")
        if status == "unknown" and source != "simulation":
            return {
                "flight_number": fn,
                "date": target_date,
                "status": "scheduled",
                "delay_minutes": 0,
                "source": source,
                "note": "No live disruption data found — flight is likely on schedule.",
            }

        return {
            "flight_number": fn,
            "date": target_date,
            "status": status,
            "delay_minutes": status_info.get("delay_minutes") or 0,
            "source": source,
        }
    except Exception as e:
        logger.exception("public_flight_status_error")
        # Graceful fallback so the landing page never shows an error
        return {
            "flight_number": fn,
            "date": target_date,
            "status": "scheduled",
            "delay_minutes": 0,
            "source": "fallback",
            "note": "Live status temporarily unavailable.",
        }
