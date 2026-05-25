"""LangGraph-oriented trip agent state (working memory).

Trip facts live in the DB; this structure holds the run transcript and tool JSON.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class TripSnapshotRef(TypedDict):
    """Pointer to persisted trip data the graph run is operating on."""

    trip_id: str
    revision: NotRequired[int]


class ToolResultRecord(TypedDict):
    """Single tool invocation result (source of truth for model reasoning)."""

    tool_name: str
    ok: bool
    payload: dict[str, Any]
    error: NotRequired[str]


class RankedOption(TypedDict):
    """One rebooking / reroute alternative after scoring (structured, not free text)."""

    option_id: str
    score: float
    summary: str
    legs: list[dict[str, Any]]
    modality: Literal["flight", "train", "mixed"]


class TripAgentState(TypedDict, total=False):
    """Default graph state; add LangGraph reducers (Annotated) when wiring the graph."""

    messages: list[Any]  # LC/AIMessage list when agent extras installed
    trip: TripSnapshotRef
    tool_results: list[ToolResultRecord]
    ranked_options: list[RankedOption]
    phase: Literal["observe", "propose", "await_confirm", "apply", "done"]
    proposal_id: NotRequired[str]
    user_confirmed: NotRequired[bool]
    last_error: NotRequired[str]
