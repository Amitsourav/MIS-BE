"""Async SQLAlchemy engine + session for the MIS's own Postgres.

This module is ONLY for the MIS database (read/write). CRM connections are
read-only and live in `app.crm` — they never touch this engine.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all MIS models."""


# SQLite (used in tests) doesn't accept QueuePool sizing args; only pass them
# for real server databases like Postgres.
_engine_kwargs: dict = {"echo": False}
if not settings.mis_database_url.startswith("sqlite"):
    _engine_kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)

engine = create_async_engine(settings.mis_database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a session, always closes it."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
