"""Google OAuth2: authorize URL, token exchange, userinfo."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from config import get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def build_google_authorize_url(state: str) -> str:
    s = get_settings()
    if not s.google_oauth_client_id:
        raise RuntimeError("Google OAuth client id is not configured")
    params = {
        "client_id": s.google_oauth_client_id,
        "redirect_uri": s.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(code: str) -> dict:
    s = get_settings()
    if not s.google_oauth_client_id or not s.google_oauth_client_secret:
        raise RuntimeError("Google OAuth is not fully configured")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_oauth_client_id,
                "client_secret": s.google_oauth_client_secret,
                "redirect_uri": s.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()


async def fetch_google_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()
