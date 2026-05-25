"""Async engine, session factory, and table creation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings
from model.base import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_engine():
    global _engine, _session_factory
    if _engine is not None:
        return
    settings = get_settings()
    _engine = create_async_engine(
        settings.database_url,
        echo=settings.debug_sql,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


def get_engine():
    _ensure_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    _ensure_engine()
    assert _session_factory is not None
    return _session_factory


async def _migrate_sqlite_users_auth(conn) -> None:
    r = await conn.execute(text("PRAGMA table_info(users)"))
    rows = r.fetchall()
    col_names = {row[1] for row in rows}
    if "google_sub" not in col_names:
        await conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(255)"))
    if "avatar_url" not in col_names:
        await conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512)"))
    if "auto_rebook" not in col_names:
        await conn.execute(text("ALTER TABLE users ADD COLUMN auto_rebook BOOLEAN DEFAULT 0"))
    if "phone_number" not in col_names:
        await conn.execute(text("ALTER TABLE users ADD COLUMN phone_number VARCHAR(20)"))

    r2 = await conn.execute(text("PRAGMA table_info(users)"))
    rows2 = r2.fetchall()
    pwd_notnull = next((row[3] for row in rows2 if row[1] == "password_hash"), 0)
    if pwd_notnull != 1:
        return

    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        await conn.execute(
            text("""
            CREATE TABLE users__new (
                id VARCHAR(36) NOT NULL,
                email VARCHAR(255) NOT NULL,
                password_hash VARCHAR(255),
                full_name VARCHAR(255),
                created_at DATETIME,
                google_sub VARCHAR(255),
                avatar_url VARCHAR(512),
                PRIMARY KEY (id)
            )
            """)
        )
        await conn.execute(
            text("""
            INSERT INTO users__new (id, email, password_hash, full_name, created_at, google_sub, avatar_url)
            SELECT id, email, password_hash, full_name, created_at, google_sub, avatar_url FROM users
            """)
        )
        await conn.execute(text("DROP TABLE users"))
        await conn.execute(text("ALTER TABLE users__new RENAME TO users"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)"))
    finally:
        await conn.execute(text("PRAGMA foreign_keys=ON"))


async def _migrate_postgres_users_auth(conn) -> None:
    r = await conn.execute(
        text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'users'
        """)
    )
    existing = {row[0] for row in r.fetchall()}
    if "google_sub" not in existing:
        await conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(255)"))
    if "avatar_url" not in existing:
        await conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512)"))

    r_null = await conn.execute(
        text("""
        SELECT is_nullable FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'users' AND column_name = 'password_hash'
        """)
    )
    row = r_null.fetchone()
    if row and row[0] == "NO":
        await conn.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))

    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)"))


async def migrate_users_auth(engine) -> None:
    settings = get_settings()
    url = settings.database_url.lower()
    async with engine.begin() as conn:
        if "sqlite" in url:
            await _migrate_sqlite_users_auth(conn)
        elif "postgresql" in url:
            await _migrate_postgres_users_auth(conn)


def _run_alembic_upgrade_sync() -> None:
    """Apply Alembic migrations (sync); use DATABASE_URL via alembic/env.py Settings."""
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parent / "alembic.ini"
    alembic_cfg = Config(str(ini))
    command.upgrade(alembic_cfg, "head")


async def init_db(base: type[DeclarativeBase] = Base) -> None:
    """Create tables (dev SQLite) or run Alembic only (Postgres production)."""
    import model  # noqa: F401 — register ORM models on Base.metadata

    settings = get_settings()
    _ensure_engine()
    if settings.database_use_alembic_only:
        await asyncio.to_thread(_run_alembic_upgrade_sync)
        return

    eng = get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
    await migrate_users_auth(eng)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_database_for_tests() -> None:
    """Clear engine singleton (sync) — use before tests with in-memory SQLite."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
