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

#: Column name -> DDL for the document registry, in the order the phases added
#: them. Every default is the honest value for a row that predates the column:
#:
#: * ``status`` -> 'indexed': every row registered before the column existed had
#:   passed ``_ensure_ingested``, so that is the only defensible backfill;
#: * ``status_reason`` -> NULL: internal context, nothing to invent;
#: * ``remote_outcome_unknown`` -> FALSE: those rows were all concluded with an
#:   answer from OpenRAG, so "nothing unknown" is a fact, not an assumption.
DOCUMENT_REGISTRY_COLUMNS: dict[str, str] = {
    "status": "VARCHAR(16) NOT NULL DEFAULT 'indexed'",
    "status_reason": "VARCHAR(200)",
    "remote_outcome_unknown": "BOOLEAN NOT NULL DEFAULT FALSE",
}


async def add_missing_registry_columns(engine: AsyncEngine) -> list[str]:
    """Add any missing ``documents`` columns, oldest phase first.

    Returns the columns it added (empty when the database is already current).
    Safe to run repeatedly, and safe on a database that has some of them — each
    column is checked on its own, which is what lets one command serve every
    phase instead of one command per phase.
    """
    added: list[str] = []
    async with engine.begin() as conn:
        existing = await conn.run_sync(_column_names, "documents")
        if existing is None:
            # No documents table yet: create_all() will build it with the columns.
            logger.info("No documents table yet; nothing to migrate")
            return added
        for column, ddl in DOCUMENT_REGISTRY_COLUMNS.items():
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
