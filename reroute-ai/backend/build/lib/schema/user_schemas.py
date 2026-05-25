"""API schemas: users and auth."""

from __future__ import annotations

import datetime
from datetime import UTC, datetime as dt

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from model.user_model import User


class UserSignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=255)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None
    created_at: datetime.datetime
    avatar_url: str | None = None
    google_account_linked: bool = False
    auto_rebook: bool = False
    phone_number: str | None = None


def user_to_public(user: User) -> UserPublic:
    created = user.created_at if user.created_at is not None else dt.now(UTC)
    return UserPublic(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        created_at=created,
        avatar_url=user.avatar_url,
        google_account_linked=user.google_sub is not None,
        auto_rebook=bool(getattr(user, "auto_rebook", False)),
        phone_number=getattr(user, "phone_number", None),
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class MessageResponse(BaseModel):
    ok: bool = True


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=500)
    new_password: str = Field(min_length=8, max_length=128)


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(None, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    auto_rebook: bool | None = Field(None, description="Auto-rebook on disruption detection")
    phone_number: str | None = Field(None, max_length=20, description="Phone for SMS notifications")


class SetPasswordRequest(BaseModel):
    """For Google-only accounts: add a password so email login / unlink-Google work."""

    new_password: str = Field(min_length=8, max_length=128)


class RefreshSessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime.datetime
    expires_at: datetime.datetime
    revoked_at: datetime.datetime | None
    remember_me: bool
