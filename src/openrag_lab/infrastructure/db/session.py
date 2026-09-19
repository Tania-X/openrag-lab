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
    """Bind the process to one database and return its engine.

    **The first call wins**, and a later call naming a *different* database is an
    error rather than a no-op. It used to be a silent no-op, which meant any
    caller that asked for its database second got the first one: ``create_all()``
    ran with no URL, so a command that called it before ``get_session()`` read and
    wrote ``data/openrag-lab.db`` no matter what ``DATABASE_URL`` said. A wrong
    database that announces itself is a bug report; one that does not is an
    incident. Call :func:`reset_db` if a switch is really intended (tests do).
    """
    global _engine, _session_factory
    url = database_url or _default_database_url()
    if _engine is not None:
        if str(_engine.url) != str(make_url(url)):
            raise RuntimeError(
                "init_db() was called with a different database than the one this "
                f"process is already using ({_engine.url} != {url}); call reset_db() "
                "first if switching is intended"
            )
        return _engine

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
    """Create missing tables only.

    Existing tables are left exactly as they are, so a local SQLite database
    from an earlier phase keeps its rows and picks up newly *added* tables.
    Columns added to an existing table are NOT applied here; Alembic is the
    long-term answer for those.
    """
    from openrag_lab.config import get_settings

    # Must ask for the configured database explicitly: init_db() with no URL
    # falls back to the default path, and whichever call arrives first is the one
    # this process is bound to.
    engine = init_db(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    from openrag_lab.config import get_settings

    init_db(get_settings().database_url)
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session
