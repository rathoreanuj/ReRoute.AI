"""Base DAO — inherit from this once SQLAlchemy session is wired."""

from __future__ import annotations

from typing import Any


class BaseDAO:
    """Minimal base; pass model class and async session when implementing."""

    def __init__(self, model: type[Any], session: Any) -> None:
        self.model = model
        self.session = session
