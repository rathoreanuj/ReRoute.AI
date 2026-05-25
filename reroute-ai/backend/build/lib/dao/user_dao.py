"""Data access: users."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dao.base_dao import BaseDAO
from model.user_model import User


class UserDAO(BaseDAO):
    def __init__(self, session: AsyncSession):
        super().__init__(User, session)

    async def get_by_id(self, user_id: str) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        result = await self.session.execute(select(User).where(User.google_sub == google_sub))
        return result.scalar_one_or_none()

    async def create(self, *, email: str, password_hash: str, full_name: str | None) -> User:
        user = User(
            id=str(uuid.uuid4()),
            email=email.lower(),
            password_hash=password_hash,
            full_name=full_name,
            created_at=datetime.now(UTC),
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def create_google_user(
        self,
        *,
        email: str,
        google_sub: str,
        full_name: str | None,
        avatar_url: str | None,
    ) -> User:
        user = User(
            id=str(uuid.uuid4()),
            email=email.lower(),
            password_hash=None,
            full_name=full_name,
            google_sub=google_sub,
            avatar_url=avatar_url,
            created_at=datetime.now(UTC),
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def save(self, user: User) -> User:
        await self.session.flush()
        await self.session.refresh(user)
        return user
