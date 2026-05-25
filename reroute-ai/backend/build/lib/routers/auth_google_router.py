"""Google OAuth: browser redirect flow."""

from __future__ import annotations

import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from service import user_service
from service.google_oauth_service import build_google_authorize_url, exchange_google_code, fetch_google_userinfo
from utils.cookie_auth import attach_auth_cookies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["auth"])


@router.get("/login")
async def google_login_start(remember_me: bool = Query(True)) -> RedirectResponse:
    s = get_settings()
    if not s.google_oauth_client_id or not s.google_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    state = secrets.token_urlsafe(32)
    r = RedirectResponse(url=build_google_authorize_url(state), status_code=302)
    same_site = s.cookie_samesite
    r.set_cookie(
        key=s.cookie_oauth_google_state_name,
        value=state,
        httponly=True,
        secure=s.cookie_secure,
        samesite=same_site,
        max_age=600,
        path="/",
    )
    r.set_cookie(
        key=s.cookie_oauth_remember_name,
        value="1" if remember_me else "0",
        httponly=True,
        secure=s.cookie_secure,
        samesite=same_site,
        max_age=600,
        path="/",
    )
    return r


@router.get("/callback")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(get_db),
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> RedirectResponse:
    s = get_settings()
    front = s.frontend_url.rstrip("/")

    def redirect_err(code: str) -> RedirectResponse:
        r = RedirectResponse(url=f"{front}/login?error={quote(code, safe='')}", status_code=302)
        r.delete_cookie(s.cookie_oauth_google_state_name, path="/")
        r.delete_cookie(s.cookie_oauth_remember_name, path="/")
        return r

    if error:
        return redirect_err("google_denied" if error == "access_denied" else "google_oauth")
    if not code or not state:
        return redirect_err("oauth_missing")

    cookie_state = request.cookies.get(s.cookie_oauth_google_state_name)
    if not cookie_state or cookie_state != state:
        return redirect_err("oauth_state")

    remember_raw = request.cookies.get(s.cookie_oauth_remember_name, "1")
    remember_me = remember_raw == "1"

    try:
        token_payload = await exchange_google_code(code)
        access = token_payload.get("access_token")
        if not access or not isinstance(access, str):
            return redirect_err("oauth_token")
        info = await fetch_google_userinfo(access)
    except Exception:
        logger.exception("google_oauth_exchange")
        return redirect_err("oauth_failed")

    google_sub = info.get("id") or info.get("sub")
    email = (info.get("email") or "").strip().lower()
    if not google_sub or not email:
        return redirect_err("oauth_profile")

    name = info.get("name")
    if isinstance(name, str):
        name = name.strip() or None
    else:
        name = None
    picture = info.get("picture")
    if not isinstance(picture, str):
        picture = None

    try:
        tokens = await user_service.complete_google_login(
            session,
            google_sub=str(google_sub),
            email=email,
            full_name=name,
            picture=picture,
            remember_me=remember_me,
        )
    except HTTPException as e:
        if e.status_code == 409:
            return redirect_err("account_conflict")
        return redirect_err("oauth_failed")

    r = RedirectResponse(url=f"{front}/dashboard", status_code=302)
    r.delete_cookie(s.cookie_oauth_google_state_name, path="/")
    r.delete_cookie(s.cookie_oauth_remember_name, path="/")
    attach_auth_cookies(
        r,
        access_token=tokens.access_token,
        refresh_plain=tokens.refresh_token_plain,
        refresh_max_age_seconds=tokens.refresh_max_age_seconds,
    )
    return r
