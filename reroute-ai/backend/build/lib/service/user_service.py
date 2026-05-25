"""Business logic: users and auth."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from dao.password_reset_dao import PasswordResetDAO
from dao.refresh_token_dao import RefreshTokenDAO
from dao.user_dao import UserDAO
from integrations.resend_client import send_email_html
from model.user_model import User
from schema.user_schemas import (
    RefreshSessionPublic,
    TokenResponse,
    UserLoginRequest,
    UserPublic,
    UserSignupRequest,
    UserUpdateRequest,
    user_to_public,
)
from utils.jwt_utils import create_access_token
from utils.password import hash_password, verify_password
from utils.token_hash import hash_refresh_token

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite may return naive datetimes; app logic uses UTC-aware `now`."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token_plain: str
    refresh_max_age_seconds: int
    expires_in: int
    refresh_expires_in: int


async def signup(payload: UserSignupRequest, session: AsyncSession) -> UserPublic:
    dao = UserDAO(session)
    if await dao.get_by_email(payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await dao.create(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
    )
    await session.commit()
    logger.info("user_signup", extra={"user_id": user.id})
    return user_to_public(user)


def _access_ttl_seconds() -> int:
    return get_settings().access_token_expire_minutes * 60


async def issue_session_tokens(user: User, session: AsyncSession, remember_me: bool) -> AuthTokens:
    settings = get_settings()
    refresh_days = (
        settings.refresh_token_remember_days if remember_me else settings.refresh_token_expire_days
    )
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=refresh_days)
    raw_refresh = secrets.token_urlsafe(48)
    family_id = str(uuid.uuid4())
    rt_dao = RefreshTokenDAO(session)
    await rt_dao.create(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        family_id=family_id,
        remember_me=remember_me,
        expires_at=expires_at,
    )
    access = create_access_token(
        subject=user.id,
        extra={"email": user.email},
    )
    refresh_max_age = int((expires_at - now).total_seconds())
    return AuthTokens(
        access_token=access,
        refresh_token_plain=raw_refresh,
        refresh_max_age_seconds=max(refresh_max_age, 60),
        expires_in=_access_ttl_seconds(),
        refresh_expires_in=refresh_days * 86400,
    )


async def login(payload: UserLoginRequest, session: AsyncSession) -> AuthTokens:
    dao = UserDAO(session)
    user = await dao.get_by_email(payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account uses Google sign-in. Use “Sign in with Google”.",
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    tokens = await issue_session_tokens(user, session, payload.remember_me)
    await session.commit()
    logger.info("user_login", extra={"user_id": user.id, "remember_me": payload.remember_me})
    return tokens


async def complete_google_login(
    session: AsyncSession,
    *,
    google_sub: str,
    email: str,
    full_name: str | None,
    picture: str | None,
    remember_me: bool,
) -> AuthTokens:
    dao = UserDAO(session)
    user = await dao.get_by_google_sub(google_sub)
    if user:
        if picture and not user.avatar_url:
            user.avatar_url = picture
        await session.flush()
        tokens = await issue_session_tokens(user, session, remember_me)
        await session.commit()
        logger.info("user_login_google", extra={"user_id": user.id})
        return tokens

    existing = await dao.get_by_email(email)
    if existing:
        if existing.google_sub is not None and existing.google_sub != google_sub:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is already linked to a different Google account",
            )
        existing.google_sub = google_sub
        if picture:
            existing.avatar_url = picture
        await session.flush()
        tokens = await issue_session_tokens(existing, session, remember_me)
        await session.commit()
        logger.info("user_login_google_link", extra={"user_id": existing.id})
        return tokens

    user = await dao.create_google_user(
        email=email,
        google_sub=google_sub,
        full_name=full_name,
        avatar_url=picture,
    )
    tokens = await issue_session_tokens(user, session, remember_me)
    await session.commit()
    logger.info("user_signup_google", extra={"user_id": user.id})
    return tokens


async def refresh_tokens(request: Request, session: AsyncSession) -> AuthTokens:
    settings = get_settings()
    raw = request.cookies.get(settings.cookie_refresh_name)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    rt_dao = RefreshTokenDAO(session)
    row = await rt_dao.get_by_hash(hash_refresh_token(raw))
    now = datetime.now(UTC)

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if row.revoked_at is not None:
        await rt_dao.revoke_family(row.family_id)
        await session.commit()
        logger.warning("refresh_token_reuse", extra={"family_id": row.family_id})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")

    if _ensure_utc(row.expires_at) <= now:
        await rt_dao.revoke(row.id)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    await rt_dao.revoke(row.id)
    settings = get_settings()
    refresh_days = (
        settings.refresh_token_remember_days if row.remember_me else settings.refresh_token_expire_days
    )
    new_expires_at = now + timedelta(days=refresh_days)
    new_raw = secrets.token_urlsafe(48)
    await rt_dao.create(
        user_id=row.user_id,
        token_hash=hash_refresh_token(new_raw),
        family_id=row.family_id,
        remember_me=row.remember_me,
        expires_at=new_expires_at,
    )
    user_dao = UserDAO(session)
    user = await user_dao.get_by_id(row.user_id)
    if not user:
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access = create_access_token(subject=user.id, extra={"email": user.email})
    await session.commit()
    refresh_max_age = int((new_expires_at - now).total_seconds())
    return AuthTokens(
        access_token=access,
        refresh_token_plain=new_raw,
        refresh_max_age_seconds=max(refresh_max_age, 60),
        expires_in=_access_ttl_seconds(),
        refresh_expires_in=refresh_days * 86400,
    )


async def logout(request: Request, session: AsyncSession) -> None:
    settings = get_settings()
    raw = request.cookies.get(settings.cookie_refresh_name)
    rt_dao = RefreshTokenDAO(session)
    if raw:
        h = hash_refresh_token(raw)
        row = await rt_dao.get_by_hash(h)
        if row and row.revoked_at is None:
            await rt_dao.revoke(row.id)
    await session.commit()


async def get_user_by_id(user_id: str, session: AsyncSession) -> User:
    dao = UserDAO(session)
    user = await dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def tokens_to_response(tokens: AuthTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        token_type="bearer",
        expires_in=tokens.expires_in,
        refresh_expires_in=tokens.refresh_expires_in,
    )


async def request_password_reset(*, email: str, session: AsyncSession) -> None:
    """Idempotent: same response path for unknown email / Google-only (no leak)."""
    settings = get_settings()
    dao = UserDAO(session)
    user = await dao.get_by_email(email)
    if not user or not user.password_hash:
        await session.commit()
        return

    pr_dao = PasswordResetDAO(session)
    await pr_dao.invalidate_all_for_user(user.id)
    raw = secrets.token_urlsafe(32)
    exp = datetime.now(UTC) + timedelta(minutes=max(5, settings.password_reset_token_expire_minutes))
    await pr_dao.create(user_id=user.id, token_hash=hash_refresh_token(raw), expires_at=exp)
    await session.commit()

    base = settings.frontend_url.rstrip("/")
    path = settings.password_reset_frontend_path
    if not path.startswith("/"):
        path = "/" + path
    link = f"{base}{path}?token={raw}"
    html = (
        f"<p>We received a request to reset your ReRoute password.</p>"
        f'<p><a href="{link}">Reset password</a> (expires in {settings.password_reset_token_expire_minutes} minutes)</p>'
        f"<p>If you did not request this, you can ignore this email.</p>"
    )
    await send_email_html(to_email=user.email, subject="ReRoute: reset your password", html=html)


async def reset_password_with_token(*, token: str, new_password: str, session: AsyncSession) -> None:
    pr_dao = PasswordResetDAO(session)
    row = await pr_dao.get_valid_by_hash(hash_refresh_token(token))
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link",
        )
    user_dao = UserDAO(session)
    user = await user_dao.get_by_id(row.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link",
        )
    user.password_hash = hash_password(new_password)
    await pr_dao.mark_used(row.id)
    rt_dao = RefreshTokenDAO(session)
    await rt_dao.revoke_all_for_user(user.id)
    await session.commit()
    logger.info("user_password_reset", extra={"user_id": user.id})


async def update_me(*, user: User, payload: UserUpdateRequest, session: AsyncSession) -> UserPublic:
    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data:
        fn = data["full_name"]
        user.full_name = (str(fn).strip() or None) if fn is not None else None
    if "avatar_url" in data:
        av = data["avatar_url"]
        user.avatar_url = (str(av).strip() or None) if av is not None else None
    if "auto_rebook" in data:
        user.auto_rebook = bool(data["auto_rebook"])
    if "phone_number" in data:
        pn = data["phone_number"]
        user.phone_number = (str(pn).strip() or None) if pn is not None else None
    dao = UserDAO(session)
    await dao.save(user)
    await session.commit()
    return user_to_public(user)


async def unlink_google(*, user: User, session: AsyncSession) -> UserPublic:
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set a password first (POST /users/me/password) before unlinking Google",
        )
    user.google_sub = None
    dao = UserDAO(session)
    await dao.save(user)
    await session.commit()
    logger.info("user_unlink_google", extra={"user_id": user.id})
    return user_to_public(user)


async def set_initial_password(*, user: User, new_password: str, session: AsyncSession) -> UserPublic:
    if user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password already set",
        )
    user.password_hash = hash_password(new_password)
    dao = UserDAO(session)
    await dao.save(user)
    await session.commit()
    logger.info("user_set_initial_password", extra={"user_id": user.id})
    return user_to_public(user)


async def list_refresh_sessions(*, user_id: str, session: AsyncSession) -> list[RefreshSessionPublic]:
    rows = await RefreshTokenDAO(session).list_active_for_user(user_id)
    out: list[RefreshSessionPublic] = []
    for r in rows:
        ca = r.created_at if r.created_at is not None else datetime.now(UTC)
        out.append(
            RefreshSessionPublic(
                id=r.id,
                created_at=ca,
                expires_at=r.expires_at,
                revoked_at=r.revoked_at,
                remember_me=r.remember_me,
            )
        )
    return out


async def revoke_refresh_session(*, user_id: str, session_id: str, session: AsyncSession) -> None:
    rt_dao = RefreshTokenDAO(session)
    row = await rt_dao.get_by_id_for_user(row_id=session_id, user_id=user_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if row.revoked_at is None:
        await rt_dao.revoke(row.id)
    await session.commit()


async def revoke_all_refresh_sessions(*, user_id: str, session: AsyncSession) -> None:
    await RefreshTokenDAO(session).revoke_all_for_user(user_id)
    await session.commit()
