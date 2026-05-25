"""Opaque refresh token hashing (store only hash)."""

from __future__ import annotations

import hashlib


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
