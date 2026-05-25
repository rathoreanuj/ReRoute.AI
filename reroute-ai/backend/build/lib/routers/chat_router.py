"""Chat endpoints — conversational trip builder with LangChain NER."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from deps import get_current_user
from model.user_model import User
from schema.chat_schemas import (
    ChatConfirmBookingRequest,
    ChatHistoryResponse,
    ChatMessageRequest,
    ChatReply,
    ChatSessionPublic,
    ChatUpdateEntitiesRequest,
    ChatUseMyInfoRequest,
)
from service import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message", response_model=ChatReply)
async def send_message(
    body: ChatMessageRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ChatReply:
    """Send a message to the chat bot; returns NER-extracted entities + bot reply."""
    return await chat_service.handle_message(
        session=session,
        user=current,
        message=body.message,
        session_id=body.session_id,
    )


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    session_id: str | None = None,
) -> ChatHistoryResponse:
    """Fetch the message history for the active (or given) chat session."""
    return await chat_service.get_history(
        session=session,
        user_id=current.id,
        user=current,
        session_id=session_id,
    )


@router.post("/new", response_model=ChatSessionPublic)
async def new_session(
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ChatSessionPublic:
    """Close any active chat session and start a fresh one."""
    return await chat_service.start_new_session(
        session=session,
        user_id=current.id,
    )


@router.post("/update-entities", response_model=ChatReply)
async def update_entities(
    body: ChatUpdateEntitiesRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ChatReply:
    """Directly update entities from the editable entity card."""
    return await chat_service.update_entities(
        session=session,
        user_id=current.id,
        session_id=body.session_id,
        entities=body.entities,
    )


@router.post("/use-my-info", response_model=ChatReply)
async def use_my_info(
    body: ChatUseMyInfoRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ChatReply:
    """Auto-fill logged-in user as passenger 1."""
    return await chat_service.use_my_info(
        session=session,
        user=current,
        session_id=body.session_id,
    )
