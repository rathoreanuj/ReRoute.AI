"""HTTP: users and auth."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from database import get_db
from deps import get_current_user
from model.user_model import User
from schema.user_schemas import (
    ForgotPasswordRequest,
    MessageResponse,
    RefreshSessionPublic,
    ResetPasswordRequest,
    SetPasswordRequest,
    TokenResponse,
    UserLoginRequest,
    UserPublic,
    UserSignupRequest,
    UserUpdateRequest,
    user_to_public,
)
from service import user_service
from utils.cookie_auth import attach_auth_cookies, clear_auth_cookies

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/signup",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
async def signup(
    body: UserSignupRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserPublic:
    return await user_service.signup(body, session)


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
)
async def login(
    response: Response,
    body: UserLoginRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    tokens = await user_service.login(body, session)
    attach_auth_cookies(
        response,
        access_token=tokens.access_token,
        refresh_plain=tokens.refresh_token_plain,
        refresh_max_age_seconds=tokens.refresh_max_age_seconds,
    )
    return user_service.tokens_to_response(tokens)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh_session(
    response: Response,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    tokens = await user_service.refresh_tokens(request, session)
    attach_auth_cookies(
        response,
        access_token=tokens.access_token,
        refresh_plain=tokens.refresh_token_plain,
        refresh_max_age_seconds=tokens.refresh_max_age_seconds,
    )
    return user_service.tokens_to_response(tokens)


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def logout(
    response: Response,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    await user_service.logout(request, session)
    clear_auth_cookies(response)
    return MessageResponse()


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def forgot_password(
    body: ForgotPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    await user_service.request_password_reset(email=body.email, session=session)
    return MessageResponse(ok=True)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def reset_password(
    body: ResetPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    await user_service.reset_password_with_token(
        token=body.token, new_password=body.new_password, session=session
    )
    return MessageResponse(ok=True)


@router.get(
    "/me",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def me(
    current: Annotated[User, Depends(get_current_user)],
) -> UserPublic:
    return user_to_public(current)


@router.patch(
    "/me",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def patch_me(
    body: UserUpdateRequest,
    current: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserPublic:
    return await user_service.update_me(user=current, payload=body, session=session)


@router.post(
    "/me/password",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def set_me_password(
    body: SetPasswordRequest,
    current: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserPublic:
    return await user_service.set_initial_password(
        user=current, new_password=body.new_password, session=session
    )


@router.post(
    "/me/unlink-google",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def unlink_google(
    current: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserPublic:
    return await user_service.unlink_google(user=current, session=session)


@router.get(
    "/me/sessions",
    response_model=list[RefreshSessionPublic],
    status_code=status.HTTP_200_OK,
)
async def list_sessions(
    current: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[RefreshSessionPublic]:
    return await user_service.list_refresh_sessions(user_id=current.id, session=session)


@router.delete(
    "/me/sessions/{session_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_session(
    session_id: str,
    current: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    await user_service.revoke_refresh_session(
        user_id=current.id, session_id=session_id, session=session
    )
    return MessageResponse(ok=True)


@router.post(
    "/me/sessions/revoke-all",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_all_sessions(
    response: Response,
    current: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    await user_service.revoke_all_refresh_sessions(user_id=current.id, session=session)
    clear_auth_cookies(response)
    return MessageResponse(ok=True)