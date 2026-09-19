"""Registry storage guarantees for the state machine (design §4/§6).

Invariant I1 lives here: **only ``INDEXED`` documents may enter a retrieval
scope**. If this test is wrong the tenant boundary can be widened by a document
that OpenRAG never confirmed, so it is the most important guard of the P1 work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.domain.identity.models import Document, Tenant, User
from openrag_lab.domain.shared.enums import DocumentStatus
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlTenantRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import seed_identity

TENANT = TenantId("t-acme")
USER = UserId("u-alice")


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await seed_identity(session)
            await SqlTenantRepository(session).save(Tenant(id=TENANT, name="Acme", slug="acme"))
            await SqlUserRepository(session).save(
                User(id=USER, tenant_id=TENANT, username="alice", password_hash="h")
            )
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


def _document(
    name: str,
    status: DocumentStatus,
    reason: str | None = None,
    remote_outcome_unknown: bool = False,
) -> Document:
    return Document(
        id=DocumentId(f"doc-{name}"),
        tenant_id=TENANT,
        stored_filename=f"acme/{name}",
        display_name=name,
        uploaded_by=USER,
        status=status,
        status_reason=reason,
        remote_outcome_unknown=remote_outcome_unknown,
    )


async def test_only_indexed_documents_enter_the_retrieval_boundary(session_factory) -> None:
    """I1: the boundary is the filename list, so未就绪的行绝不能进去。"""
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(_document("ready.md", DocumentStatus.INDEXED))
        await repo.save(_document("in-flight.md", DocumentStatus.INDEXING))
        await repo.save(_document("broken.md", DocumentStatus.FAILED, reason="boom"))
        await repo.save(_document("going.md", DocumentStatus.DELETING))
        await repo.save(_document("gone.md", DocumentStatus.DELETED, reason="removed 3 chunk(s)"))
        await session.commit()

    async with session_factory() as session:
        filenames = await SqlDocumentRepository(session).list_stored_filenames(TENANT)

    assert filenames == ["acme/ready.md"], f"边界只能含 INDEXED, 实际 {filenames}"


async def test_the_api_listing_still_shows_every_state(session_factory) -> None:
    """决策 2b: 列表返回全部 + 状态, 否则"上传成功却看不到"会被当成丢件。"""
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(_document("ready.md", DocumentStatus.INDEXED))
        await repo.save(_document("broken.md", DocumentStatus.FAILED, reason="upstream 500"))
        await session.commit()

    async with session_factory() as session:
        documents = await SqlDocumentRepository(session).list_by_tenant(TENANT)

    assert {d.display_name for d in documents} == {"ready.md", "broken.md"}
    by_name = {d.display_name: d for d in documents}
    assert by_name["broken.md"].status is DocumentStatus.FAILED
    assert by_name["broken.md"].status_reason == "upstream 500"


async def test_status_and_reason_survive_a_round_trip_and_an_update(session_factory) -> None:
    """状态与原因都要真的落库(否则状态机只是内存里的字符串)。"""
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(_document("x.md", DocumentStatus.INDEXING))
        await session.commit()

    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        document = await repo.find_by_stored_filename(TENANT, "acme/x.md")
        assert document is not None and document.status is DocumentStatus.INDEXING

        document.mark_failed("OpenRAGError: boom")
        await repo.save(document)
        await session.commit()

    async with session_factory() as session:
        document = await SqlDocumentRepository(session).find_by_stored_filename(
            TENANT, "acme/x.md"
        )

    assert document is not None
    assert document.status is DocumentStatus.FAILED
    assert document.status_reason == "OpenRAGError: boom"


async def test_the_listing_hides_tombstones_but_shows_an_in_flight_delete(
    session_factory,
) -> None:
    """墓碑是账, 不是文档(P2 决策 1a)。

    已经删掉的再出现在列表里会读成"删除没生效"; 而正在删的必须留着 —— 那一瞬间
    它还在检索边界之外、但删除尚未落定, 藏起来等于藏了一件仍在进行的事。
    """
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(_document("ready.md", DocumentStatus.INDEXED))
        await repo.save(_document("going.md", DocumentStatus.DELETING))
        await repo.save(_document("gone.md", DocumentStatus.DELETED, reason="removed 3 chunk(s)"))
        await session.commit()

    async with session_factory() as session:
        documents = await SqlDocumentRepository(session).list_by_tenant(TENANT)

    assert {d.display_name for d in documents} == {"ready.md", "going.md"}
    by_name = {d.display_name: d for d in documents}
    assert by_name["going.md"].status is DocumentStatus.DELETING


async def test_the_tombstone_row_is_still_there(session_factory) -> None:
    """删除只改状态, 不删行 —— 行是"这个文件名曾经被登记过"的唯一记录。"""
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(_document("gone.md", DocumentStatus.DELETING))
        await session.commit()

    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        document = await repo.find_by_stored_filename(TENANT, "acme/gone.md")
        assert document is not None and document.status is DocumentStatus.DELETING

        document.mark_deleted(confirmed=False, detail="no verdict from OpenRAG: Timeout")
        await repo.save(document)
        await session.commit()

    async with session_factory() as session:
        document = await SqlDocumentRepository(session).find_by_stored_filename(
            TENANT, "acme/gone.md"
        )

    assert document is not None, "墓碑必须留在库里"
    assert document.status is DocumentStatus.DELETED
    assert document.status_reason == "no verdict from OpenRAG: Timeout"
    assert document.remote_outcome_unknown is True, "确认与否必须落库(对账的工作清单)"


async def test_an_unknown_outcome_survives_a_round_trip_on_failed_rows_too(
    session_factory,
) -> None:
    """上传超时同样要留痕: 远端可能已经写入, 这条标记就是对账要不要探活的依据。"""
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(
            _document(
                "maybe.md",
                DocumentStatus.FAILED,
                reason="no verdict from OpenRAG: Timeout",
                remote_outcome_unknown=True,
            )
        )
        await session.commit()

    async with session_factory() as session:
        document = await SqlDocumentRepository(session).find_by_stored_filename(
            TENANT, "acme/maybe.md"
        )

    assert document is not None
    assert document.remote_outcome_unknown is True
