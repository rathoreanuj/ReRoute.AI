"""HTTP: monitor dashboard aggregates."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from deps import get_current_user
from model.user_model import User
from schema.monitor_schemas import MonitorStatusResponse, MonitorTickResponse
from service import monitor_service

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get(
    "/status",
    response_model=MonitorStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def monitor_status(
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> MonitorStatusResponse:
    return await monitor_service.build_status(session=session, user_id=current.id)


@router.post(
    "/tick",
    response_model=MonitorTickResponse,
    status_code=status.HTTP_200_OK,
)
async def monitor_tick(
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> MonitorTickResponse:
    """
    Acknowledge a monitor cycle; v1 reuses the same DB aggregation as GET /status.
    Heavy checks stay on POST /agent/propose.
    """
    st = await monitor_service.build_status(session=session, user_id=current.id)
    return MonitorTickResponse(
        ok=True,
        message="Monitor tick recorded (v1 aggregates only; run agent propose for live checks).",
        status=st,
    )
