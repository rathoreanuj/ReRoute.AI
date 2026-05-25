"""HTTP: trips CRUD + ticket upload extraction."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from deps import get_current_user
from model.user_model import User
from schema.itinerary_schemas import TripDetailPublic
from schema.trip_schemas import TripCreateRequest, TripPublic, TripUpdateRequest
from service import trip_service

router = APIRouter(prefix="/trips", tags=["trips"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/extract-from-ticket", status_code=status.HTTP_200_OK)
async def extract_from_ticket(
    file: UploadFile = File(...),
    current: User = Depends(get_current_user),
):
    """Upload a boarding pass, e-ticket, or booking confirmation. Returns extracted trip entities."""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file.content_type}. Use JPEG, PNG, WebP, GIF, or PDF.",
        )

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum 10 MB.",
        )

    from service.ticket_extract_service import (
        extract_from_image_bytes,
        extract_from_pdf_bytes,
        extracted_to_trip_entities,
    )

    if file.content_type == "application/pdf":
        result = await extract_from_pdf_bytes(data)
    else:
        result = await extract_from_image_bytes(data, file.content_type or "image/jpeg")

    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.get("error", "Could not extract data from ticket."),
        )

    extracted = result.get("extracted", {})
    entities = extracted_to_trip_entities(extracted)

    return {
        "ok": True,
        "extracted_raw": extracted,
        "entities": entities,
        "message": f"Extracted {len(entities)} fields from ticket.",
    }


@router.post("", response_model=TripPublic, status_code=status.HTTP_201_CREATED)
async def create_trip(
    body: TripCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> TripPublic:
    return await trip_service.create_trip(user=current, payload=body, session=session)


@router.get("", response_model=list[TripPublic], status_code=status.HTTP_200_OK)
async def list_trips(
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> list[TripPublic]:
    return await trip_service.list_trips(user_id=current.id, session=session)


@router.get("/{trip_id}/detail", response_model=TripDetailPublic, status_code=status.HTTP_200_OK)
async def get_trip_detail(
    trip_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> TripDetailPublic:
    return await trip_service.get_trip_detail(user_id=current.id, trip_id=trip_id, session=session)


@router.get("/{trip_id}", response_model=TripPublic, status_code=status.HTTP_200_OK)
async def get_trip(
    trip_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> TripPublic:
    return await trip_service.get_trip(user_id=current.id, trip_id=trip_id, session=session)


@router.patch("/{trip_id}", response_model=TripPublic, status_code=status.HTTP_200_OK)
async def update_trip(
    trip_id: str,
    body: TripUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> TripPublic:
    return await trip_service.update_trip(
        user_id=current.id,
        trip_id=trip_id,
        payload=body,
        session=session,
    )


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Response:
    await trip_service.delete_trip(user_id=current.id, trip_id=trip_id, session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
