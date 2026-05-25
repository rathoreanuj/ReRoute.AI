"""Agent proposal + confirm endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from deps import get_current_user
from model.user_model import User
from schema.agent_schemas import (
    AgentConfirmRequest,
    AgentConfirmResponse,
    AgentProposeAsyncRequest,
    AgentProposeJobAccepted,
    AgentProposeJobStatus,
    AgentProposeRequest,
    AgentProposeResponse,
)
from service import agent_service

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post(
    "/propose/async",
    response_model=AgentProposeJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def propose_async(
    body: AgentProposeAsyncRequest,
    current: Annotated[User, Depends(get_current_user)],
) -> AgentProposeJobAccepted:
    """Enqueue propose without opening a DB session (Celery + Redis ownership)."""
    return agent_service.enqueue_async_propose(
        user_id=current.id,
        trip_id=body.trip_id,
        simulate_disruption=body.simulate_disruption,
    )


@router.post(
    "/propose",
    response_model=None,
    responses={
        200: {"model": AgentProposeResponse, "description": "Synchronous propose result"},
        202: {"model": AgentProposeJobAccepted, "description": "Background job enqueued"},
    },
)
async def propose(
    body: AgentProposeRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    if body.async_mode:
        out = agent_service.enqueue_async_propose(
            user_id=current.id,
            trip_id=body.trip_id,
            simulate_disruption=body.simulate_disruption,
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=out.model_dump())

    return await agent_service.propose_for_trip(
        session=session,
        user_id=current.id,
        trip_id=body.trip_id,
        simulate_disruption=body.simulate_disruption,
    )


@router.get(
    "/propose/jobs/{task_id}",
    response_model=AgentProposeJobStatus,
    status_code=status.HTTP_200_OK,
)
async def propose_job_status(
    task_id: str,
    current: Annotated[User, Depends(get_current_user)],
) -> AgentProposeJobStatus:
    return agent_service.get_async_propose_job_status(task_id=task_id, user_id=current.id)


@router.post(
    "/confirm",
    response_model=AgentConfirmResponse,
    status_code=status.HTTP_200_OK,
)
async def confirm(
    body: AgentConfirmRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> AgentConfirmResponse:
    return await agent_service.confirm_and_apply(session=session, user_id=current.id, body=body)
