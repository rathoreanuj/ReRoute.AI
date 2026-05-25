"""httpOnly cookie helpers for browser sessions."""

from __future__ import annotations

from fastapi import Response

from config import get_settings


def attach_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_plain: str,
    refresh_max_age_seconds: int,
) -> None:
    s = get_settings()
    same_site = s.cookie_samesite
    response.set_cookie(
        key=s.cookie_access_name,
        value=access_token,
        httponly=True,
        secure=s.cookie_secure,
        samesite=same_site,
        max_age=s.access_token_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key=s.cookie_refresh_name,
        value=refresh_plain,
        httponly=True,
        secure=s.cookie_secure,
        samesite=same_site,
        max_age=refresh_max_age_seconds,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(s.cookie_access_name, path="/")
    response.delete_cookie(s.cookie_refresh_name, path="/")
