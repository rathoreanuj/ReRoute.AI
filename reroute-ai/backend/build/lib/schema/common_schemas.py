"""Shared Pydantic response wrappers — expand when API contract stabilizes."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class BaseResponse(BaseModel, Generic[T]):
    """Standard envelope for JSON APIs."""

    success: bool = True
    data: T | None = None
