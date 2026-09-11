"""Async database engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from openrag_lab.infrastructure.db import models  # noqa: F401  # register tables
from openrag_lab.infrastructure.db.base import Base

DEFAULT_DB_PATH = Path("data/openrag-lab.db")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _default_database_url() -> str:
    return f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"


def init_db(database_url: str | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    url = database_url or _default_database_url()
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_async_engine(url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_db() -> None:
    """Reset cached engine/session factory (mainly for tests)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


async def create_all() -> None:
    """Create any missing tables.

    Alembic is the long-term path; until it lands this only creates tables
    that do not exist yet, so a local SQLite database created by an earlier
    phase picks up new tables without losing data.
    """
    engine = init_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    from openrag_lab.config import get_settings

    init_db(get_settings().database_url)
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session
