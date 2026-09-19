"""The one-off migration that adds the document registry columns (design §8).

`create_all()` never alters an existing table, so a database created before the
state machine would 500 on the first upload. These tests build precisely that
database — the *old* schema, with rows in it — and migrate it.

One command serves every phase: it checks each column on its own, so a database
that stopped halfway (or that only predates P2) is completed rather than
rejected.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from openrag_lab.infrastructure.db.migrations import add_missing_registry_columns

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
        added = await add_missing_registry_columns(engine)

        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda c: [col["name"] for col in inspect(c).get_columns("documents")])
            rows = (
                await conn.execute(
                    text("SELECT status, status_reason, remote_outcome_unknown FROM documents")
                )
            ).all()

        assert added == ["status", "status_reason", "remote_outcome_unknown"]
        assert {"status", "status_reason", "remote_outcome_unknown"} <= set(columns)
        assert rows == [("indexed", None, 0), ("indexed", None, 0)]
    finally:
        await engine.dispose()


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    """跑第二次必须什么都不做(部署脚本会重复执行)。"""
    engine = await _old_database(tmp_path / "old.db")
    try:
        assert await add_missing_registry_columns(engine) == [
            "status",
            "status_reason",
            "remote_outcome_unknown",
        ]
        assert await add_missing_registry_columns(engine) == []
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
        assert await add_missing_registry_columns(engine) == [
            "status_reason",
            "remote_outcome_unknown",
        ]
    finally:
        await engine.dispose()


async def test_migration_on_a_fresh_database_is_a_no_op(tmp_path: Path) -> None:
    """新库没有 documents 表: 交给 create_all(), 迁移不报错也不建表。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        assert await add_missing_registry_columns(engine) == []
        async with engine.connect() as conn:
            assert not await conn.run_sync(lambda c: inspect(c).has_table("documents"))
    finally:
        await engine.dispose()


async def test_migration_completes_a_p1_database(tmp_path: Path) -> None:
    """P1 升级到 P2 的真实路径: 已有 status/status_reason, 只缺 P2 那一列。

    这正是线上库的形态 —— P1 的迁移已经跑过, 现在要补 `remote_outcome_unknown`。
    回填成 FALSE 是事实而不是假设: P1 期间每一行都是拿到答复后才定终态的。
    """
    engine = await _old_database(tmp_path / "old.db")
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "ALTER TABLE documents ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'indexed'"
            )
            await conn.exec_driver_sql("ALTER TABLE documents ADD COLUMN status_reason VARCHAR(200)")

        assert await add_missing_registry_columns(engine) == ["remote_outcome_unknown"]
        async with engine.connect() as conn:
            rows = (
                await conn.execute(text("SELECT status, remote_outcome_unknown FROM documents"))
            ).all()
        assert rows == [("indexed", 0), ("indexed", 0)]
        # 再跑一次仍然什么都不做
        assert await add_missing_registry_columns(engine) == []
    finally:
        await engine.dispose()
