"""Schema migrations for databases created before a column existed.

``create_all()`` only creates missing *tables*; it never adds columns to an
existing one (see its docstring, and the note that Alembic is the long-term
answer). Until Alembic lands, the few columns we add are handled here:
explicitly, idempotently, and with a command an operator can run on purpose
(design: docs/document-registry-state-design.md §8).
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

#: Column name -> DDL for the registry-state migration (P1).
#: ``status`` is backfilled with 'indexed': every row registered before the
#: column existed had passed ``_ensure_ingested``, so that is the only honest
#: value. ``status_reason`` is internal (it quotes upstream error text).
DOCUMENT_STATUS_COLUMNS: dict[str, str] = {
    "status": "VARCHAR(16) NOT NULL DEFAULT 'indexed'",
    "status_reason": "VARCHAR(200)",
}


async def add_document_status_columns(engine: AsyncEngine) -> list[str]:
    """Add the registry status columns to ``documents`` if they are missing.

    Returns the columns it added (empty when the database is already current).
    Safe to run repeatedly, and safe on a database where only one of the two
    columns exists — each column is checked on its own.
    """
    added: list[str] = []
    async with engine.begin() as conn:
        existing = await conn.run_sync(_column_names, "documents")
        if existing is None:
            # No documents table yet: create_all() will build it with the columns.
            logger.info("No documents table yet; nothing to migrate")
            return added
        for column, ddl in DOCUMENT_STATUS_COLUMNS.items():
            if column in existing:
                continue
            await conn.execute(text(f"ALTER TABLE documents ADD COLUMN {column} {ddl}"))
            added.append(column)
            logger.info("Added documents.%s", column)
    return added


def _column_names(sync_connection, table: str) -> set[str] | None:
    """Column names of ``table``; ``None`` when the table does not exist."""
    inspector = inspect(sync_connection)
    if not inspector.has_table(table):
        return None
    return {column["name"] for column in inspector.get_columns(table)}
