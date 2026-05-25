"""Pydantic schemas for the chat endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(None, description="Resume an existing session; null = auto-resolve active session")


class ChatActionRequest(BaseModel):
    """Trigger an action from the chat (e.g. run agent, confirm booking)."""
    session_id: str
    action: str = Field(..., description="run_agent | confirm_booking")
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatUpdateEntitiesRequest(BaseModel):
    """Directly patch accumulated entities from the editable card."""
    session_id: str
    entities: dict[str, Any]


class ChatConfirmBookingRequest(BaseModel):
    """Confirm a rebooking option directly from chat."""
    session_id: str
    proposal_id: str
    selected_option_id: str
    acknowledge_disruption_uncertainty: bool = False


class ChatUseMyInfoRequest(BaseModel):
    """Auto-fill logged-in user as passenger 1."""
    session_id: str


class ChatNewSessionRequest(BaseModel):
    """Force start a fresh chat session."""
    pass


# ── Response ──────────────────────────────────────────────────

class QuickReplyChip(BaseModel):
    """A suggested quick-reply button."""
    label: str
    value: str
    icon: str | None = None  # lucide icon name hint for frontend


class ChatMessagePublic(BaseModel):
    id: str
    role: str
    content: str
    extracted_entities: dict[str, Any] | None = None
    # Structured data embedded in message (option cards, entity card, etc.)
    card_type: str | None = None  # "options" | "entity_summary" | "booking_confirmed" | "agent_progress"
    card_data: dict[str, Any] | None = None
    created_at: datetime


class ChatSessionPublic(BaseModel):
    id: str
    phase: str
    entities: dict[str, Any]
    trip_id: str | None = None
    proposal_id: str | None = None
    created_at: datetime


class ChatReply(BaseModel):
    """Single response from POST /chat/message."""
    session: ChatSessionPublic
    reply: ChatMessagePublic
    entities: dict[str, Any]
    missing_fields: list[str] = Field(default_factory=list)
    ready_to_save: bool = False
    quick_replies: list[QuickReplyChip] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    session: ChatSessionPublic
    messages: list[ChatMessagePublic]
    quick_replies: list[QuickReplyChip] = Field(default_factory=list)


class ChatActionResponse(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    quick_replies: list[QuickReplyChip] = Field(default_factory=list)


class AgentProgressEvent(BaseModel):
    """SSE event for agent progress streaming."""
    step: str  # "flight_status" | "weather" | "search" | "classify" | "rank" | "complete" | "error"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    progress_pct: int = 0  # 0-100
