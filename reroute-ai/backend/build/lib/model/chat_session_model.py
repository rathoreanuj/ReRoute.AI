"""Chat session model — one per user conversation thread."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.sqlite import JSON

from model.base import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Accumulated entity state (JSON) — grows as the bot extracts info
    entities = Column(JSON, nullable=False, default=dict)
    # "collecting" | "ready_to_save" | "trip_created" | "agent_running" | "done"
    phase = Column(String(32), nullable=False, default="collecting")
    # trip_id once the trip is persisted
    trip_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_chat_sessions_user_id", "user_id"),
    )
