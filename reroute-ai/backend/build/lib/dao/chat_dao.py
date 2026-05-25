"""Data access for chat sessions and messages."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.chat_message_model import ChatMessage
from model.chat_session_model import ChatSession


class ChatDAO:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Sessions ──────────────────────────────────────────────

    async def get_or_create_active_session(self, user_id: str) -> ChatSession:
        """Return the most recent non-done session, or create a new one."""
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.phase != "done")
            .order_by(ChatSession.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            return row
        new_session = ChatSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            entities={},
            phase="collecting",
        )
        self.session.add(new_session)
        await self.session.flush()
        return new_session

    async def get_session(self, session_id: str, user_id: str) -> ChatSession | None:
        stmt = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_session(
        self,
        session_id: str,
        *,
        entities: dict[str, Any] | None = None,
        phase: str | None = None,
        trip_id: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if entities is not None:
            values["entities"] = entities
        if phase is not None:
            values["phase"] = phase
        if trip_id is not None:
            values["trip_id"] = trip_id
        if not values:
            return
        stmt = update(ChatSession).where(ChatSession.id == session_id).values(**values)
        await self.session.execute(stmt)

    async def close_session(self, session_id: str) -> None:
        stmt = update(ChatSession).where(ChatSession.id == session_id).values(phase="done")
        await self.session.execute(stmt)

    # ── Messages ──────────────────────────────────────────────

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        extracted_entities: dict[str, Any] | None = None,
    ) -> ChatMessage:
        # Get next seq
        stmt = select(func.coalesce(func.max(ChatMessage.seq), 0)).where(
            ChatMessage.session_id == session_id
        )
        result = await self.session.execute(stmt)
        next_seq = (result.scalar() or 0) + 1

        msg = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            extracted_entities=extracted_entities,
            seq=next_seq,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def list_messages(self, session_id: str, limit: int = 100) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
