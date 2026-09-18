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


def _document(name: str, status: DocumentStatus, reason: str | None = None) -> Document:
    return Document(
        id=DocumentId(f"doc-{name}"),
        tenant_id=TENANT,
        stored_filename=f"acme/{name}",
        display_name=name,
        uploaded_by=USER,
        status=status,
        status_reason=reason,
    )


async def test_only_indexed_documents_enter_the_retrieval_boundary(session_factory) -> None:
    """I1: the boundary is the filename list, so未就绪的行绝不能进去。"""
    async with session_factory() as session:
        repo = SqlDocumentRepository(session)
        await repo.save(_document("ready.md", DocumentStatus.INDEXED))
        await repo.save(_document("in-flight.md", DocumentStatus.INDEXING))
        await repo.save(_document("broken.md", DocumentStatus.FAILED, reason="boom"))
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
