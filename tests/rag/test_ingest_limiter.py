"""Concurrency limits for the blocking OpenRAG calls that hold a worker thread.

Every gateway call is blocking, so the application offloads it with
``to_thread.run_sync``. The anyio default pool is 40 threads and is **shared**
by all offloaded calls, while an upload can hold its thread for the whole ingest
timeout (300s by default). Without a separate ceiling, a burst of uploads parks
the pool and searches queue behind them.

These tests pin the two halves of that policy:

* uploads (and deletes) are capped by ``INGEST_MAX_CONCURRENCY``;
* reads keep using the default pool, so a saturated upload limiter does not
  delay a search.
"""

from __future__ import annotations

import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openrag_lab.application.rag import document_service as document_service_module
from openrag_lab.application.rag.document_service import DocumentService
from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.application.rag.search_service import SearchService
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Tenant, User
from openrag_lab.domain.shared.ids import TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlTenantRepository,
    SqlUserRepository,
)

ACME = "t-acme"
ALICE = "u-alice"


class BlockingGateway:
    """Gateway whose mutations block until the test releases them.

    Records how many ingests were inside the gateway at the same time, which is
    exactly what the limiter is supposed to bound. Methods run on anyio worker
    threads (that is the whole point of the code under test), so the counters
    take a lock and the anyio event is only ever *set* on the loop.
    """

    def __init__(self) -> None:
        self.gate: anyio.Event = anyio.Event()
        self._lock = threading.Lock()
        self.entered = 0
        self.max_inside = 0
        self.finished = 0

    def ingest_document(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.entered += 1
            self.max_inside = max(self.max_inside, self.entered)
        # Blocking call, like the real client waiting on an ingest task.
        anyio.from_thread.run(self.gate.wait)
        with self._lock:
            self.entered -= 1
            self.finished += 1
        return {"status": "completed", "failed_files": 0}

    def find_document_id(self, **kwargs: Any) -> str | None:
        return "orag-doc-1"

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return {"results": [{"filename": "acme/report.md"}]}

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        return {"response": "ok"}

    def delete_document(self, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "deleted_chunks": 0}


@pytest.fixture(autouse=True)
def _pinned_openrag_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "openrag_api_key", "orag_test_key", raising=False)


@asynccontextmanager
async def _session_factory(tmp_path: Path):
    """A file-backed database holding one active tenant and one user.

    Deliberately not ``:memory:`` with ``StaticPool``: that pool hands every
    session the *same* connection, and these tests run uploads concurrently —
    each on its own session, the way a request is served. A file lets each
    session open its own connection; ``timeout`` makes SQLite wait for the
    writer lock instead of failing fast.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'limiter.db'}",
        connect_args={"timeout": 30},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            tenant = Tenant(id=TenantId(ACME), name="Acme", slug="acme")
            await SqlTenantRepository(session).save(tenant)
            await SqlUserRepository(session).save(
                User(
                    id=UserId(ALICE),
                    tenant_id=tenant.id,
                    username="alice",
                    password_hash="hashed",
                )
            )
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


@asynccontextmanager
async def _staged_uploads(count: int = 4):
    """One real temp file per concurrent upload.

    Distinct names on purpose: the registry has a UNIQUE(tenant_id,
    stored_filename) guard, so uploading the *same* name concurrently would
    (correctly) fail one of them and test the wrong thing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        paths: list[Path] = []
        for index in range(count):
            path = Path(tmp) / f"report-{index}.md"
            path.write_bytes(b"# report\n")
            paths.append(path)
        yield paths


async def _upload(factory, gateway: BlockingGateway, path: Path) -> None:
    """One upload, on its own session — the way a request is served."""
    async with factory() as session:
        await DocumentService(session, gateway).upload_document(
            actor_user_id=ALICE,
            actor_tenant_id=ACME,
            uploaded_by=ALICE,
            filename=path.name,
            path=path,
        )


async def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    """Poll a thread-visible condition from the event loop."""
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.02)


@pytest.fixture
def _small_ingest_limiter(monkeypatch: pytest.MonkeyPatch):
    """Shrink the limiter so the test does not need 8 concurrent uploads."""
    limiter = anyio.CapacityLimiter(2)
    monkeypatch.setattr(document_service_module, "_INGEST_LIMITER", limiter)
    return limiter


def test_uploads_are_capped_by_the_ingest_limiter(_small_ingest_limiter, tmp_path: Path) -> None:
    """At most ``INGEST_MAX_CONCURRENCY`` uploads may hold a worker thread."""

    async def scenario() -> None:
        gateway = BlockingGateway()
        async with _session_factory(tmp_path) as factory, _staged_uploads() as paths:
            async with anyio.create_task_group() as tg:
                for path in paths:
                    tg.start_soon(_upload, factory, gateway, path)
                # Let the first uploads reach the gateway, then look at how many
                # of the four made it inside at once.
                await _wait_until(lambda: gateway.max_inside >= 2)
                await anyio.sleep(0.2)  # any 3rd/4th would have arrived by now
                assert gateway.max_inside == 2, (
                    f"ingest limiter let {gateway.max_inside} uploads through at once"
                )
                assert gateway.entered == 2, "the other two should be queued"
                # Release the gate and let the task group drain: the uploads
                # still have to write the registry, so "left the gateway" is
                # not the same as "finished". Leaving the group early would
                # dispose the engine under them.
                gateway.gate.set()

    anyio.run(scenario)


def test_a_saturated_upload_limiter_does_not_block_search(
    _small_ingest_limiter, tmp_path: Path
) -> None:
    """Reads stay on the default pool: uploads queue, searches do not."""

    async def scenario() -> None:
        gateway = BlockingGateway()
        async with _session_factory(tmp_path) as factory, _staged_uploads() as paths:
            async with anyio.create_task_group() as tg:
                for path in paths:  # four uploads, more than the limiter allows
                    tg.start_soon(_upload, factory, gateway, path)
                await _wait_until(lambda: gateway.entered == 2)

                # Both upload slots are busy; a search must still finish, on its
                # own session and its own (default-pool) worker thread.
                async with factory() as session:
                    search = SearchService(RetrievalScopeResolver(session), gateway)
                    with anyio.fail_after(5):
                        payload = await search.search(
                            actor_user_id=ALICE, actor_tenant_id=ACME, query="报表"
                        )
                assert payload["results"], "search should have returned its results"

                gateway.gate.set()  # the task group drains the uploads below

    anyio.run(scenario)
