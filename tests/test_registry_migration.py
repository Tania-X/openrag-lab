"""The one-off migration that adds the registry status columns (design §8).

`create_all()` never alters an existing table, so a database created before the
state machine would 500 on the first upload. These tests build precisely that
database — the *old* schema, with rows in it — and migrate it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from openrag_lab.infrastructure.db.migrations import add_document_status_columns

OLD_SCHEMA = """
CREATE TABLE documents (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    stored_filename VARCHAR(512) NOT NULL,
    display_name VARCHAR(512) NOT NULL,
    uploaded_by VARCHAR(64) NOT NULL,
    mimetype VARCHAR(255),
    size_bytes BIGINT,
    openrag_document_id VARCHAR(128),
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_documents_tenant_filename UNIQUE (tenant_id, stored_filename)
)
"""


async def _old_database(path: Path, rows: int = 2):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(OLD_SCHEMA)
        for index in range(rows):
            await conn.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, stored_filename, display_name,"
                    " uploaded_by) VALUES (:id, 't-acme', :stored, :name, 'u-alice')"
                ),
                {"id": f"doc-{index}", "stored": f"acme/r{index}.md", "name": f"r{index}.md"},
            )
    return engine


async def test_migration_adds_the_columns_and_backfills_indexed(tmp_path: Path) -> None:
    """存量行必须被回填成 `indexed` —— 迁移前每一行都过了 `_ensure_ingested`。"""
    engine = await _old_database(tmp_path / "old.db")
    try:
        added = await add_document_status_columns(engine)

        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda c: [col["name"] for col in inspect(c).get_columns("documents")])
            rows = (await conn.execute(text("SELECT status, status_reason FROM documents"))).all()

        assert added == ["status", "status_reason"]
        assert {"status", "status_reason"} <= set(columns)
        assert rows == [("indexed", None), ("indexed", None)]
    finally:
        await engine.dispose()


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    """跑第二次必须什么都不做(部署脚本会重复执行)。"""
    engine = await _old_database(tmp_path / "old.db")
    try:
        assert await add_document_status_columns(engine) == ["status", "status_reason"]
        assert await add_document_status_columns(engine) == []
    finally:
        await engine.dispose()


async def test_migration_completes_a_partially_migrated_database(tmp_path: Path) -> None:
    """只差一列的情况也要能补(例如上一次迁移中途失败)。"""
    engine = await _old_database(tmp_path / "old.db")
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "ALTER TABLE documents ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'indexed'"
            )
        assert await add_document_status_columns(engine) == ["status_reason"]
    finally:
        await engine.dispose()


async def test_migration_on_a_fresh_database_is_a_no_op(tmp_path: Path) -> None:
    """新库没有 documents 表: 交给 create_all(), 迁移不报错也不建表。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        assert await add_document_status_columns(engine) == []
        async with engine.connect() as conn:
            assert not await conn.run_sync(lambda c: inspect(c).has_table("documents"))
    finally:
        await engine.dispose()
