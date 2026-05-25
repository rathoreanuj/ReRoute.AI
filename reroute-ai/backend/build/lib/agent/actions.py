"""Action pipeline: observe → propose → confirm gate → apply (mutations only after confirm)."""

from __future__ import annotations

from enum import StrEnum


class ActionPhase(StrEnum):
    OBSERVE = "observe"
    PROPOSE = "propose"
    AWAIT_CONFIRM = "await_confirm"
    APPLY = "apply"
    DONE = "done"


def describe_pipeline() -> str:
    """Human-readable flow for docs and logs."""
    return (
        "Observe (tools) → Propose (structured plan) → Persist proposal → "
        "User confirms → Apply (itinerary_service updates DB / simulated PNR)"
    )
