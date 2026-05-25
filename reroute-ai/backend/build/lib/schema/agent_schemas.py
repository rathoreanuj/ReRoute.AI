"""API-facing schemas for agent proposals and confirmations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentProposeRequest(BaseModel):
    trip_id: str = Field(..., description="Trip to analyze")
    simulate_disruption: str | None = Field(
        None,
        description="Optional demo flag e.g. cancel|delay|divert to drive mock integrations",
    )
    async_mode: bool = Field(
        False,
        description="If true, enqueue Celery job and poll GET .../propose/jobs/{task_id} (requires Redis).",
    )


class AgentProposeAsyncRequest(BaseModel):
    """Same as sync propose body; used by POST /agent/propose/async (no DB session dependency)."""

    trip_id: str = Field(..., description="Trip to analyze")
    simulate_disruption: str | None = Field(
        None,
        description="Optional demo flag e.g. cancel|delay|divert to drive mock integrations",
    )


class RankedOptionDTO(BaseModel):
    option_id: str
    score: float
    summary: str
    legs: list[dict[str, Any]] = Field(default_factory=list)
    modality: str = "flight"
    llm_explanation: str | None = Field(None, description="GPT-generated explanation of why this option is good/bad")
    price_display: str | None = Field(None, description="Formatted price e.g. 'USD 245.00'")
    arrival_display: str | None = Field(None, description="Formatted arrival e.g. 'Mar 28, 6:15 PM'")
    stops: int = Field(0, description="Number of stops/connections")
    duration_minutes: int | None = Field(None, description="Total travel time in minutes")


class AgentProposeResponse(BaseModel):
    proposal_id: str
    phase: str
    requires_user_review: bool = False
    disruption_summary: str | None = None
    llm_disruption_narrative: str | None = Field(None, description="GPT-generated natural language disruption explanation")
    ranked_options: list[RankedOptionDTO] = Field(default_factory=list)
    tool_trace_summary: list[str] = Field(default_factory=list)
    cascade_preview: dict[str, Any] | None = None
    cascade_narrative: str | None = Field(None, description="GPT-generated cascade impact explanation")
    compensation_draft: dict[str, Any] | None = None
    notification_status: dict[str, Any] | None = None
    search_meta: dict[str, Any] | None = None
    offers_expired_at: str | None = Field(None, description="ISO timestamp when Duffel offers likely expire (~30 min from search)")
    price_comparison: dict[str, Any] | None = Field(None, description="Price delta between original and cheapest option")
    passenger_validation: dict[str, Any] | None = Field(None, description="Validation warnings for passenger data")


class AgentConfirmRequest(BaseModel):
    proposal_id: str
    selected_option_id: str
    acknowledge_disruption_uncertainty: bool = False


class AgentConfirmResponse(BaseModel):
    applied: bool
    itinerary_revision: int | None = None
    message: str
    duffel_order_id: str | None = None
    email_sent: bool | None = None
    email_queued: bool | None = Field(
        None,
        description="True when delivery was handed off to Celery (email_via_celery).",
    )


class AgentProposeJobAccepted(BaseModel):
    task_id: str
    state: Literal["queued"] = "queued"
    poll_path: str


class AgentProposeJobStatus(BaseModel):
    task_id: str
    state: str
    result: AgentProposeResponse | None = None
    error: str | None = None
