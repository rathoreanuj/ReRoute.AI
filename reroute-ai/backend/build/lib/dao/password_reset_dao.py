"""Password reset token persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.password_reset_token_model import PasswordResetToken


class PasswordResetDAO:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, user_id: str, token_hash: str, expires_at: datetime) -> PasswordResetToken:
        row = PasswordResetToken(
            id=str(uuid.uuid4()),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_valid_by_hash(self, token_hash: str) -> PasswordResetToken | None:
        now = datetime.now(UTC)
        r = await self.session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        return r.scalar_one_or_none()

    async def mark_used(self, row_id: str) -> None:
        await self.session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.id == row_id)
            .values(used_at=datetime.now(UTC)),
        )

    async def invalidate_all_for_user(self, user_id: str) -> None:
        await self.session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
            .values(used_at=datetime.now(UTC)),
        )
