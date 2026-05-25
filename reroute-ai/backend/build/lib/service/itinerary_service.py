"""Apply itinerary changes after user confirm (cascade + snapshot hints).

Duffel order creation stays in agent_service; this layer returns structured apply metadata.
"""

from __future__ import annotations

from typing import Any


async def apply_rebooking_plan(*, trip_context: dict[str, Any], option: dict[str, Any]) -> dict[str, Any]:
    """
    Simulate itinerary mutation and return fields merged into trip.snapshot by the agent.
    """
    return {
        "applied_message": "Itinerary updated (simulated).",
        "selected_offer_id": option.get("duffel_offer_id"),
        "arrival_time": option.get("arrival_time"),
    }
