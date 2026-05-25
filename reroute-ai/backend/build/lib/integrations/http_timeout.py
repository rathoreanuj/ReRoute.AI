"""Shared httpx.Timeout values from Settings (connect + read for outbound integrations)."""

from __future__ import annotations

import httpx

from config import get_settings


def integration_timeout() -> httpx.Timeout:
    s = get_settings()
    return httpx.Timeout(
        connect=s.http_timeout_connect,
        read=s.http_timeout_read,
        write=s.http_timeout_read,
        pool=s.http_timeout_connect,
    )
