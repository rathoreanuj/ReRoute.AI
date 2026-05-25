"""Individual chat messages within a session."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON

from model.base import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    # "user" | "assistant" | "system"
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    # Entities extracted from this specific message (if any)
    extracted_entities = Column(JSON, nullable=True)
    # Ordering within session
    seq = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_chat_messages_session_id", "session_id"),
    )
