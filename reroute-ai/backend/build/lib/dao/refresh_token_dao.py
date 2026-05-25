"""Refresh token persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.refresh_token_model import RefreshToken


class RefreshTokenDAO:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        family_id: str,
        remember_me: bool,
        expires_at: datetime,
    ) -> RefreshToken:
        row = RefreshToken(
            id=str(uuid.uuid4()),
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            remember_me=remember_me,
            expires_at=expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        r = await self.session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        return r.scalar_one_or_none()

    async def revoke(self, row_id: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == row_id)
            .values(revoked_at=datetime.now(UTC)),
        )

    async def revoke_family(self, family_id: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC)),
        )

    async def revoke_all_for_user(self, user_id: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC)),
        )

    async def list_active_for_user(self, user_id: str) -> list[RefreshToken]:
        now = datetime.now(UTC)
        r = await self.session.execute(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.created_at.desc())
        )
        return list(r.scalars().all())

    async def get_by_id_for_user(self, *, row_id: str, user_id: str) -> RefreshToken | None:
        r = await self.session.execute(
            select(RefreshToken).where(RefreshToken.id == row_id, RefreshToken.user_id == user_id)
        )
        return r.scalar_one_or_none()
