"""Tests for tenant-scoped document management.

Same boundary discipline as retrieval: whatever the client sends, the request
that reaches OpenRAG must be scoped to one tenant, and the registry must never
claim a document that is not actually indexed.
"""

from __future__ import annotations

import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.client import OpenRAGError
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Tenant, TenantUserRole, User
from openrag_lab.domain.rag.ports import RagOutcomeUnknownError
from openrag_lab.domain.shared.enums import DocumentStatus, GlobalRoleName
from openrag_lab.domain.shared.ids import TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlRoleRepository,
    SqlTenantRepository,
    SqlTenantUserRoleRepository,
    SqlUserGlobalRoleRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import seed_identity
from openrag_lab.infrastructure.db.session import get_session
from openrag_lab.interfaces.api.deps import CurrentUser, get_current_user, get_rag_gateway
from openrag_lab.interfaces.api.errors import register_exception_handlers
from openrag_lab.interfaces.api.routers import documents

TEST_API_KEY = "orag_test_key"
ACME_TENANT = CurrentUser(user_id="u-acme", tenant_id="t-acme", username="alice", display_name="Alice")
GLOBEX_TENANT = CurrentUser(user_id="u-globex", tenant_id="t-globex", username="bob", display_name="Bob")
ROOT = CurrentUser(user_id="u-root", tenant_id="t-acme", username="root", display_name="Root")


@pytest.fixture(autouse=True)
def _pinned_openrag_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "openrag_api_key", TEST_API_KEY, raising=False)


class FakeDocumentGateway:
    """Records ingest/delete calls and can be told to fail.

    The delete outcome is selectable because the port contract has three of
    them: a settled removal, a settled "nothing was there", and a call that
    ended without a verdict (``RagOutcomeUnknownError``).
    """

    def __init__(self) -> None:
        self.ingested: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self.task: dict[str, Any] = {"status": "completed", "failed_files": 0}
        self.error: Exception | None = None
        #: Returned by ``delete_document`` when ``error`` is unset.
        self.delete_result: dict[str, Any] = {
            "deleted_chunks": 3,
            "already_absent": False,
        }
        self.staged_path: Path | None = None
        self.lookups: list[dict[str, Any]] = []
        self.searches: list[dict[str, Any]] = []
        self.listings: list[dict[str, Any]] = []
        self.remote_files: list[str] = []
        self.document_id: str | None = "orag-doc-1"

    def ingest_document(self, **kwargs: Any) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        # Read the staged file the way the real client does: by path, from a
        # worker thread, while the upload request is still in flight.
        path = kwargs["path"]
        kwargs["uploaded_bytes"] = path.read_bytes()
        self.staged_path = path
        self.ingested.append(kwargs)
        return self.task

    def delete_document(self, **kwargs: Any) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.deleted.append(kwargs)
        return dict(self.delete_result)

    def find_document_id(self, **kwargs: Any) -> str | None:
        self.lookups.append(kwargs)
        return self.document_id

    def list_document_filenames(self, **kwargs: Any) -> list[str]:
        self.listings.append(kwargs)
        return list(self.remote_files)

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.searches.append(kwargs)
        return {"results": [{"filename": "acme/whatever.md"}]}


@asynccontextmanager
async def _build_client(
    *,
    actor: CurrentUser | None,
    documents_seed: list[tuple[str, str]] | None = None,
    seeded_document_id: str | None = None,
    seeded_status: str = "indexed",
    tenant_role: str | None = "user",
    include_search: bool = False,
    registry: dict[str, Any] | None = None,
):
    """Build an app over a fresh in-memory registry.

    ``registry`` (optional out-parameter) receives the session factory and the
    document repository, so a test can inspect rows the API deliberately hides —
    notably ``DELETED`` tombstones.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        if registry is not None:
            registry["session_factory"] = session_factory
        async with session_factory() as session:
            await seed_identity(session)
            tenant_repo = SqlTenantRepository(session)
            user_repo = SqlUserRepository(session)
            role_repo = SqlTenantUserRoleRepository(session)
            global_role_repo = SqlUserGlobalRoleRepository(session)
            document_repo = SqlDocumentRepository(session)
            if registry is not None:
                registry["documents"] = document_repo
            role = (
                await SqlRoleRepository(session).find_by_name(tenant_role)
                if tenant_role is not None
                else None
            )

            for tid, slug, username in (
                ("t-acme", "acme", "alice"),
                ("t-globex", "globex", "bob"),
            ):
                tenant = Tenant(id=TenantId(tid), name=slug.title(), slug=slug)
                await tenant_repo.save(tenant)
                user = User(
                    id=UserId(f"u-{slug}"),
                    tenant_id=tenant.id,
                    username=username,
                    password_hash="hashed",
                )
                await user_repo.save(user)
                if role is not None:
                    await role_repo.assign_role(
                        TenantUserRole(
                            tenant_id=tenant.id, user_id=user.id, role_id=role.id
                        )
                    )
            await session.flush()

            root = User(
                id=UserId("u-root"),
                tenant_id=TenantId("t-acme"),
                username="root",
                password_hash="hashed",
            )
            await user_repo.save(root)
            from openrag_lab.domain.identity.models import UserGlobalRole
            from openrag_lab.domain.shared.ids import GlobalRoleId

            await global_role_repo.assign_global_role(
                UserGlobalRole(
                    user_id=root.id,
                    global_role_id=GlobalRoleId(
                        f"global-role-{GlobalRoleName.SUPER_ADMIN.value}"
                    ),
                )
            )

            for tid, display_name in documents_seed or []:
                seeded_tenant = await tenant_repo.find_by_id(TenantId(tid))
                assert seeded_tenant is not None
                stored = seeded_tenant.scope_filename(display_name)
                from openrag_lab.domain.identity.models import Document
                from openrag_lab.domain.shared.enums import DocumentStatus
                from openrag_lab.domain.shared.ids import DocumentId

                await document_repo.save(
                    Document(
                        id=DocumentId(stored),
                        tenant_id=seeded_tenant.id,
                        stored_filename=stored,
                        display_name=display_name,
                        uploaded_by=UserId(f"u-{seeded_tenant.slug}"),
                        mimetype="text/markdown",
                        size_bytes=10,
                        openrag_document_id=seeded_document_id,
                        status=DocumentStatus(seeded_status),
                    )
                )
            await session.commit()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(documents.router)
        if include_search:
            from openrag_lab.interfaces.api.routers import search as search_router

            app.include_router(search_router.router)

        gateway = FakeDocumentGateway()

        async def _session_override():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_rag_gateway] = lambda: gateway
        if actor is not None:
            app.dependency_overrides[get_current_user] = lambda: actor

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        async with client:
            yield client, gateway
    finally:
        await engine.dispose()


def _upload(name: str, content: bytes = b"# doc\n") -> dict[str, Any]:
    """Return the httpx ``files=`` mapping for one uploaded document."""
    return {"file": (name, content, "text/markdown")}


# ── listing ────────────────────────────────────────────────────────────────


async def test_listing_requires_authentication() -> None:
    async with _build_client(actor=None) as (client, _):
        response = await client.get("/api/documents")
    assert response.status_code == 401


async def test_viewer_can_list_documents() -> None:
    async with _build_client(actor=ACME_TENANT, tenant_role="viewer") as (client, _):
        response = await client.get("/api/documents")
    assert response.status_code == 200


async def test_listing_is_denied_without_the_read_permission() -> None:
    async with _build_client(actor=ACME_TENANT, tenant_role=None) as (client, _):
        response = await client.get("/api/documents")
    assert response.status_code == 403


async def test_listing_returns_only_the_callers_tenant() -> None:
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "mine.md"), ("t-globex", "theirs.md")],
    ) as (client, _):
        response = await client.get("/api/documents")
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert body["files"][0]["display_name"] == "mine.md"
    assert body["files"][0]["stored_filename"] == "acme/mine.md"


async def test_listing_another_tenant_is_rejected_for_non_super_admin() -> None:
    async with _build_client(actor=ACME_TENANT) as (client, _):
        response = await client.get("/api/documents", params={"tenant_id": "t-globex"})
    assert response.status_code == 403


async def test_super_admin_can_list_another_tenant() -> None:
    async with _build_client(
        actor=ROOT, documents_seed=[("t-globex", "theirs.md")]
    ) as (client, _):
        response = await client.get("/api/documents", params={"tenant_id": "t-globex"})
    body = response.json()
    assert response.status_code == 200
    assert [f["stored_filename"] for f in body["files"]] == ["globex/theirs.md"]


# ── upload ────────────────────────────────────────────────────────────────


async def test_upload_stores_the_namespaced_name_and_registers_it() -> None:
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post(
            "/api/documents/ingest", files=_upload("report.md")
        )
        assert response.status_code == 201
        assert gateway.ingested[-1]["stored_filename"] == "acme/report.md"
        assert gateway.ingested[-1]["api_key"] == TEST_API_KEY
        # The id is read back after ingest, since the task does not report it.
        assert gateway.lookups[-1]["stored_filename"] == "acme/report.md"
        assert response.json()["openrag_document_id"] == "orag-doc-1"

        listing = (await client.get("/api/documents")).json()
    assert response.json()["stored_filename"] == "acme/report.md"
    assert [f["display_name"] for f in listing["files"]] == ["report.md"]


async def test_upload_without_a_filename_is_rejected() -> None:
    """A file part with no name never reaches the domain rule (422 from parsing)."""
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post(
            "/api/documents/ingest", files={"file": ("", b"# doc\n", "text/markdown")}
        )
    assert response.status_code == 422
    assert gateway.ingested == []


async def test_upload_keeps_the_file_readable_until_ingestion_finishes() -> None:
    """The staged file must still exist while the gateway is reading it."""
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post(
            "/api/documents/ingest", files=_upload("report.md", b"# unique body\n")
        )
    assert response.status_code == 201
    assert gateway.ingested[-1]["uploaded_bytes"] == b"# unique body\n"
    # ...and the temporary file is cleaned up afterwards.
    assert gateway.staged_path is not None
    assert not gateway.staged_path.exists()


@pytest.mark.parametrize(
    "name",
    [r"C:\fakepath\report.md", "../report.md", "nested/dir/report.md", " report.md "],
)
async def test_upload_reduces_a_client_path_to_a_basename(name: str) -> None:
    """Clients send paths; the boundary reduces them to a plain name."""
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post("/api/documents/ingest", files=_upload(name))
    assert response.status_code == 201
    assert gateway.ingested[-1]["stored_filename"] == "acme/report.md"


@pytest.mark.parametrize("name", ["   ", "/", "..", ".hidden", "dir/", "dir/.hidden"])
async def test_upload_rejects_names_with_no_usable_basename(name: str) -> None:
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post("/api/documents/ingest", files=_upload(name))
    assert response.status_code == 400
    assert gateway.ingested == []


async def test_upload_replaces_an_existing_document_instead_of_failing() -> None:
    async with _build_client(
        actor=ACME_TENANT, documents_seed=[("t-acme", "report.md")]
    ) as (client, _):
        before = (await client.get("/api/documents")).json()["files"][0]
        await asyncio.sleep(0.01)  # keep the timestamps distinguishable
        response = await client.post("/api/documents/ingest", files=_upload("report.md"))
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 201
    assert listing["total"] == 1
    # Replacing is a modification, and the contract exposes updated_at.
    after = listing["files"][0]
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]


async def test_failed_ingestion_leaves_a_discoverable_failed_row() -> None:
    """失败不再"什么都不留", 而是留一条 FAILED 行(状态机 P1 的契约变更)。

    旧契约是"失败的 ingest 不留登记行"—— 代价是失败只存在于日志里, 而进程崩溃
    造成的不一致根本不可见。现在:
    - 登记行存在、状态 `failed`、带原因(供对账/人工处理);
    - 但它**不进检索边界**(I1: 只有 `INDEXED` 参与 `data_sources`)。
    """
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        gateway.task = {"status": "failed", "failed_files": 1, "error": "unsupported"}
        response = await client.post("/api/documents/ingest", files=_upload("bad.md"))
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 400
    assert "did not complete" in response.json()["detail"]
    assert listing["total"] == 1, "失败必须留痕, 而不是静默消失"
    assert listing["files"][0]["status"] == "failed"


async def test_openrag_failure_during_upload_is_a_bad_gateway() -> None:
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        gateway.error = OpenRAGError("boom")
        response = await client.post("/api/documents/ingest", files=_upload("x.md"))
    assert response.status_code == 502


async def test_upload_larger_than_the_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 8, raising=False)
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post(
            "/api/documents/ingest", files=_upload("big.md", b"x" * 64)
        )
    assert response.status_code == 413
    assert gateway.ingested == []


async def test_rejected_oversized_upload_leaves_no_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 413 path must not leak the staging file it created."""
    created: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def recording_mkstemp(*args: Any, **kwargs: Any):
        descriptor, name = real_mkstemp(*args, **kwargs)
        created.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("openrag_lab.interfaces.api.routers.documents.tempfile.mkstemp", recording_mkstemp)
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 8, raising=False)
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post(
            "/api/documents/ingest", files=_upload("big.md", b"x" * 64)
        )
    assert response.status_code == 413
    assert created and all(not path.exists() for path in created)
    assert gateway.ingested == []


@pytest.mark.parametrize("name", ["payload.exe", "archive.zip", "noextension"])
async def test_upload_rejects_unsupported_formats(name: str) -> None:
    """The same whitelist the CLI uses; nothing reaches OpenRAG otherwise."""
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post("/api/documents/ingest", files=_upload(name))
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
    assert gateway.ingested == []


async def test_reupload_refreshes_the_document_id() -> None:
    """Replacing with different content changes the id OpenRAG assigns."""
    async with _build_client(
        actor=ACME_TENANT, documents_seed=[("t-acme", "report.md")]
    ) as (client, gateway):
        gateway.document_id = "orag-doc-2"
        await client.post("/api/documents/ingest", files=_upload("report.md"))
        listing = (await client.get("/api/documents")).json()
    assert listing["files"][0]["openrag_document_id"] == "orag-doc-2"


async def test_upload_tolerates_a_missing_document_id() -> None:
    """A lookup miss must not fail the upload nor clear a known id."""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        seeded_document_id="orag-doc-known",
    ) as (client, gateway):
        gateway.document_id = None
        response = await client.post("/api/documents/ingest", files=_upload("report.md"))
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 201
    assert listing["files"][0]["openrag_document_id"] == "orag-doc-known"


async def test_failed_ingestion_is_a_bad_request_not_a_server_error() -> None:
    """The contract maps an ingestion failure to 400, not a bare 500."""
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        gateway.task = {"status": "completed", "failed_files": 1}
        response = await client.post("/api/documents/ingest", files=_upload("broken.md"))
    assert response.status_code == 400
    assert "did not complete" in response.json()["detail"]


async def test_upload_requires_the_upload_permission() -> None:
    async with _build_client(actor=ACME_TENANT, tenant_role="viewer") as (client, _):
        response = await client.post("/api/documents/ingest", files=_upload("x.md"))
    assert response.status_code == 403


async def test_upload_to_another_tenant_is_rejected_for_non_super_admin() -> None:
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        response = await client.post(
            "/api/documents/ingest",
            files=_upload("x.md"),
            data={"tenant_id": "t-globex"},
        )
    assert response.status_code == 403
    assert gateway.ingested == []


# ── delete ────────────────────────────────────────────────────────────────


async def test_delete_removes_from_openrag_and_keeps_a_tombstone() -> None:
    """P2 契约: 远端删掉, 本地留一行 `deleted` 墓碑, 列表里看不到它。

    行不能被删掉 —— 它是"这个文件名曾经登记过"的唯一记录, 丢了就再也无法对账
    (设计 §5.2)。
    """
    registry: dict[str, Any] = {}
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        seeded_document_id="orag-before-delete",  # 有 id 才能验"删除把它作废了"
        tenant_role="developer",
        registry=registry,
    ) as (client, gateway):
        response = await client.delete("/api/documents/report.md")
        listing = (await client.get("/api/documents")).json()
        # 引擎随 fixture 一起销毁(内存库), 所以墓碑必须在块内查
        async with registry["session_factory"]() as session:
            row = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "report.md",
        "stored_filename": "acme/report.md",
        "deleted_chunks": 3,
        "confirmed": True,
    }
    assert gateway.deleted[-1]["stored_filename"] == "acme/report.md"
    assert listing["total"] == 0, "墓碑不进用户列表"
    assert row is not None, "行必须留着(墓碑)"
    assert row.status is DocumentStatus.DELETED
    assert row.status_reason == "removed 3 chunk(s)"
    assert row.remote_outcome_unknown is False
    assert row.openrag_document_id is None, "远端已不存在, id 必须作废"


async def test_delete_of_an_unregistered_document_is_not_found() -> None:
    async with _build_client(actor=ACME_TENANT, tenant_role="developer") as (
        client,
        gateway,
    ):
        response = await client.delete("/api/documents/never-uploaded.md")
    assert response.status_code == 404
    assert gateway.deleted == []


async def test_delete_cannot_reach_another_tenants_document() -> None:
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-globex", "theirs.md")],
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete("/api/documents/theirs.md")
    # acme/theirs.md was never registered, so the other tenant's copy is safe.
    assert response.status_code == 404
    assert gateway.deleted == []


async def test_super_admin_may_delete_in_another_tenant() -> None:
    """Documented design intent: super_admin is the global root.

    It reaches any active tenant exactly as it does for search and user
    creation; the resolver is the single place that decides this.
    """
    async with _build_client(
        actor=ROOT,
        documents_seed=[("t-globex", "theirs.md")],
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete(
            "/api/documents/theirs.md", params={"tenant_id": "t-globex"}
        )
    assert response.status_code == 200
    assert gateway.deleted[-1]["stored_filename"] == "globex/theirs.md"
    assert response.json()["stored_filename"] == "globex/theirs.md"


async def test_delete_requires_the_delete_permission() -> None:
    async with _build_client(
        actor=ACME_TENANT, documents_seed=[("t-acme", "report.md")], tenant_role="viewer"
    ) as (client, _):
        response = await client.delete("/api/documents/report.md")
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["a%5Cb.md", "%20report.md", "report.md%20", ".hidden.md"])
async def test_delete_rejects_names_that_are_not_plain(path: str) -> None:
    """Reducing a malformed name would delete a *different* document."""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "b.md"), ("t-acme", "report.md")],
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete(f"/api/documents/{path}")
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 400
    assert gateway.deleted == []
    # Nothing was removed by the rejected request.
    assert listing["total"] == 2


async def test_delete_accepts_a_name_with_an_inner_space() -> None:
    """Internal spaces are legal in a display name; only edge whitespace is not."""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "quarterly report.md")],
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete("/api/documents/quarterly%20report.md")
    assert response.status_code == 200
    assert gateway.deleted[-1]["stored_filename"] == "acme/quarterly report.md"


async def test_reupload_moves_the_uploader_of_record() -> None:
    """Whoever last uploaded the document owns the registry row."""
    async with _build_client(
        actor=ROOT, documents_seed=[("t-acme", "report.md")], tenant_role="developer"
    ) as (client, _):
        await client.post("/api/documents/ingest", files=_upload("report.md"))
        listing = (await client.get("/api/documents")).json()
    assert listing["files"][0]["uploaded_by"] == ROOT.user_id


class TestStatusCodeContract:
    """The status codes api-contract.md §2.6/§2.7 promises, pinned end to end.

    Domain errors are mapped centrally in interfaces/api/errors.py; the routers
    deliberately do not catch them, so these assertions are the evidence that a
    rejected upload is a 400 and not an unhandled 500.
    """

    async def test_unsupported_format_is_400(self) -> None:
        async with _build_client(actor=ACME_TENANT) as (client, _):
            response = await client.post(
                "/api/documents/ingest", files=_upload("payload.exe")
            )
        assert response.status_code == 400

    async def test_invalid_filename_is_400(self) -> None:
        async with _build_client(actor=ACME_TENANT) as (client, _):
            response = await client.post(
                "/api/documents/ingest", files=_upload("dir/.hidden")
            )
        assert response.status_code == 400

    async def test_failed_ingestion_is_400(self) -> None:
        async with _build_client(actor=ACME_TENANT) as (client, gateway):
            gateway.task = {"status": "failed", "failed_files": 1}
            response = await client.post(
                "/api/documents/ingest", files=_upload("doc.md")
            )
        assert response.status_code == 400

    async def test_oversized_upload_is_413(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "max_upload_bytes", 4, raising=False)
        async with _build_client(actor=ACME_TENANT) as (client, _):
            response = await client.post(
                "/api/documents/ingest", files=_upload("doc.md", b"too long")
            )
        assert response.status_code == 413

    async def test_openrag_transport_failure_is_502(self) -> None:
        async with _build_client(actor=ACME_TENANT) as (client, gateway):
            gateway.error = OpenRAGError("boom")
            response = await client.post(
                "/api/documents/ingest", files=_upload("doc.md")
            )
        assert response.status_code == 502

    async def test_unregistered_delete_is_404(self) -> None:
        async with _build_client(actor=ACME_TENANT, tenant_role="developer") as (
            client,
            _,
        ):
            response = await client.delete("/api/documents/never-uploaded.md")
        assert response.status_code == 404


# ── 登记表状态机(P1): 意图先行 / 晋升 / 失败 / 冲突 ────────────────────────


async def test_upload_promotes_the_row_to_indexed() -> None:
    async with _build_client(actor=ACME_TENANT) as (client, _):
        response = await client.post("/api/documents/ingest", files=_upload("ok.md"))
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 201
    assert response.json()["status"] == "indexed"
    assert listing["files"][0]["status"] == "indexed"


async def test_promotion_failure_leaves_the_row_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """晋升失败(本地写坏)时, 行必须留在 INDEXING —— 那正是对账能发现的状态。

    这是设计 §5.1 的兜底论证: 意图已提交, 之后的任何崩溃路径都落在非终态一侧。
    """
    calls = {"n": 0}
    original_save = SqlDocumentRepository.save

    async def failing_on_promotion(self, document):
        calls["n"] += 1
        if calls["n"] >= 2:            # 第 1 次是意图, 第 2 次是晋升
            raise RuntimeError("registry write failed")
        await original_save(self, document)

    monkeypatch.setattr(SqlDocumentRepository, "save", failing_on_promotion)

    async with _build_client(actor=ACME_TENANT, include_search=True) as (client, _):
        with pytest.raises(RuntimeError):
            await client.post("/api/documents/ingest", files=_upload("ok.md"))
        listing = (await client.get("/api/documents")).json()
        scope = (await client.post("/api/search", json={"query": "x"})).json()

    # 行在(意图已提交), 状态停在 INDEXING, 且**不进检索边界**
    assert [f["status"] for f in listing["files"]] == ["indexing"]
    assert scope["scope"]["document_count"] == 0


async def test_failed_upload_can_be_retried_on_the_same_row() -> None:
    """FAILED → (重试) → INDEXED: 行不变(created_at 保留), 状态收敛。"""
    async with _build_client(
        actor=ACME_TENANT, documents_seed=[("t-acme", "again.md")], seeded_status="failed"
    ) as (client, gateway):
        before = (await client.get("/api/documents")).json()["files"][0]
        response = await client.post("/api/documents/ingest", files=_upload("again.md"))
        after = (await client.get("/api/documents")).json()["files"][0]

    assert response.status_code == 201
    assert response.json()["status"] == "indexed"
    assert after["id"] == before["id"], "重试应复用同一行"
    assert after["created_at"] == before["created_at"]


async def test_upload_of_a_name_being_indexed_is_a_conflict() -> None:
    """同一名字正在索引 → 409(而不是两个调用方一起驱动远端状态)。"""
    async with _build_client(
        actor=ACME_TENANT, documents_seed=[("t-acme", "busy.md")], seeded_status="indexing"
    ) as (client, _):
        response = await client.post("/api/documents/ingest", files=_upload("busy.md"))
    assert response.status_code == 409
    # 文案要点名是哪个状态在挡: 契约里有三种 409, 运维得能分清
    assert "indexing" in response.json()["detail"]


async def test_delete_of_a_name_being_indexed_is_a_conflict() -> None:
    """删除也要让路: 否则会与在途上传的晋升互相踩(P2 的 DELETING 才是正解)。

    用 developer 角色: `documents:delete` 在 user 角色里没有, 否则会先被 403 挡掉。
    """
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "busy.md")],
        seeded_status="indexing",
        tenant_role="developer",
    ) as (client, _):
        response = await client.delete("/api/documents/busy.md")
    assert response.status_code == 409


async def test_a_name_being_indexed_is_not_searchable() -> None:
    """在途文档不进检索边界 —— 即使它有登记行。"""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "busy.md")],
        seeded_status="indexing",
        include_search=True,
    ) as (client, _):
        payload = (await client.post("/api/search", json={"query": "x"})).json()
    assert payload["scope"]["document_count"] == 0


async def test_concurrent_uploads_of_one_name_produce_one_winner() -> None:
    """同名并发上传: 一个成功、一个 409, 不产生两行。

    用"网关阻塞 + 事件"把并发变成确定性顺序: A 的意图提交后阻塞在 ingest,
    B 此时到达 → 必须 409(而不是也去调 OpenRAG)。
    """
    import anyio

    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        in_flight = anyio.Event()
        release = anyio.Event()
        original_ingest = gateway.ingest_document

        def blocking_ingest(**kwargs):
            in_flight.set()
            # 在 worker 线程里等一下, 让 B 有机会进来(它必须被 409 挡住)
            anyio.from_thread.run(release.wait)
            return original_ingest(**kwargs)

        gateway.ingest_document = blocking_ingest

        results: dict[str, int] = {}

        async def first_upload() -> None:
            response = await client.post("/api/documents/ingest", files=_upload("same.md"))
            results["first"] = response.status_code

        async def second_upload() -> None:
            await in_flight.wait()
            response = await client.post("/api/documents/ingest", files=_upload("same.md"))
            results["second"] = response.status_code
            release.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(first_upload)
            tg.start_soon(second_upload)

        listing = (await client.get("/api/documents")).json()

    assert results == {"first": 201, "second": 409}, results
    assert listing["total"] == 1, "并发不能产生重复行"
    assert listing["files"][0]["status"] == "indexed"


# ── P2: 逻辑删除 / 墓碑 / 删除侧的比赛 ──────────────────────────────────────


async def test_a_delete_without_a_verdict_is_reported_as_unconfirmed() -> None:
    """远端超时: 算删除(名字已离开检索边界), 但必须回报 confirmed=false。

    回答一句光秃秃的"已删除"等于让猜测冒充事实 —— 这正是本仓一直在防的那类错。
    """
    registry: dict[str, Any] = {}
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        tenant_role="developer",
        registry=registry,
    ) as (client, gateway):
        gateway.error = RagOutcomeUnknownError("timeout", operation="delete")
        response = await client.delete("/api/documents/report.md")
        async with registry["session_factory"]() as session:
            row = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )

    assert response.status_code == 200
    assert response.json()["confirmed"] is False
    assert response.json()["deleted_chunks"] == 0, "不知道删了几个, 就不能编一个数字"
    assert row is not None and row.status is DocumentStatus.DELETED
    assert row.remote_outcome_unknown is True, "未确认必须留痕(对账的工作清单)"


async def test_a_refused_delete_leaves_the_row_deleting_and_answers_502() -> None:
    """真拒绝(远端 5xx): 什么也没删掉, 行停在 DELETING —— 非终态, 可发现可重试。"""
    registry: dict[str, Any] = {}
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        tenant_role="developer",
        registry=registry,
    ) as (client, gateway):
        gateway.error = OpenRAGError("boom", status_code=500)
        response = await client.delete("/api/documents/report.md")
        async with registry["session_factory"]() as session:
            row = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )

    assert response.status_code == 502
    assert row is not None
    assert row.status is DocumentStatus.DELETING, "删除失败不能回退成 INDEXED(那是撒谎)"
    assert row.remote_outcome_unknown is False, "500 是明确的拒绝, 不是未知"
    # 卡住的行必须说明为什么卡住(与 FAILED 路径对称), 否则运维只能去翻日志
    assert row.status_reason is not None and "boom" in row.status_reason


async def test_a_tombstone_is_not_found_and_is_not_deleted_again() -> None:
    """P2 决策 1a: 删一个已经是墓碑的名字 → 404, 且不再碰远端。

    重试未确认的删除是对账(P3)的职责 —— 把重试挂回请求路径, 等于让一个死掉的
    请求成为唯一的修复机会。
    """
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "gone.md")],
        seeded_status="deleted",
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete("/api/documents/gone.md")

    assert response.status_code == 404
    assert gateway.deleted == [], "墓碑不该触发第二次远端删除"


async def test_a_delete_cannot_start_twice() -> None:
    """另一个删除正在驱动远端 → 409(否则两个调用方一起删同一份内容)。"""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "going.md")],
        seeded_status="deleting",
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete("/api/documents/going.md")

    assert response.status_code == 409
    assert "deleting" in response.json()["detail"]
    assert gateway.deleted == []


async def test_an_upload_cannot_start_while_a_delete_is_in_flight() -> None:
    """上传撞删除 → 409: 一个要建、一个要拆, 同时驱动远端必然互相覆盖。"""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "going.md")],
        seeded_status="deleting",
    ) as (client, gateway):
        response = await client.post("/api/documents/ingest", files=_upload("going.md"))

    assert response.status_code == 409
    assert "deleting" in response.json()["detail"]
    assert gateway.ingested == [], "冲突要在调 OpenRAG 之前就挡住"


async def test_a_deleted_name_can_be_re_uploaded_and_resurrects_its_row() -> None:
    """同名重传复活墓碑: 同一行、同一个 id、created_at 保留 —— 键没有被释放。"""
    registry: dict[str, Any] = {}
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        tenant_role="developer",
        registry=registry,
    ) as (client, gateway):
        await client.delete("/api/documents/report.md")
        async with registry["session_factory"]() as session:
            tombstone = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )
        assert tombstone is not None

        response = await client.post("/api/documents/ingest", files=_upload("report.md"))
        listing = (await client.get("/api/documents")).json()
        async with registry["session_factory"]() as session:
            revived = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )

    assert response.status_code == 201
    assert response.json()["status"] == "indexed"
    assert revived is not None
    assert revived.id == tombstone.id, "复活同一行, 不是新登记一行"
    assert revived.created_at == tombstone.created_at, "首次登记时间要保留"
    assert revived.status_reason is None
    assert listing["total"] == 1, "复活后就该重新出现在列表里"


async def test_a_tombstone_never_widens_the_retrieval_scope() -> None:
    """墓碑同样不进检索边界(它只是没被删行, 不是仍可检索)。"""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "gone.md"), ("t-acme", "ready.md")],
        seeded_status="indexed",
        include_search=True,
        tenant_role="developer",  # 删除需要 documents:delete
    ) as (client, gateway):
        # 先删掉一个, 再看检索范围
        deleted = await client.delete("/api/documents/gone.md")
        assert deleted.status_code == 200, deleted.text
        payload = (await client.post("/api/search", json={"query": "x"})).json()

    assert payload["scope"]["document_count"] == 1
    assert gateway.searches[-1]["filters"]["data_sources"] == ["acme/ready.md"]


async def test_a_resurrected_row_does_not_keep_the_deleted_document_id() -> None:
    """评审提的那个场景: 删除后重传, 而这次 id 回查没查到东西。

    `mark_indexed` 只在**真的收到** id 时才覆盖(best effort 查不到就保持原值),
    所以如果删除时不清 id, 复活后的行会一直挂着一个已经被删掉的远端 id ——
    而它在 API 响应里是可见的。删除那一刻就该作废它。
    """
    registry: dict[str, Any] = {}
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        seeded_document_id="orag-before-delete",
        tenant_role="developer",
        registry=registry,
    ) as (client, gateway):
        assert (await client.delete("/api/documents/report.md")).status_code == 200

        gateway.document_id = None  # 回查未命中(best effort 会漏)
        revived = await client.post("/api/documents/ingest", files=_upload("report.md"))
        async with registry["session_factory"]() as session:
            row = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )

    assert revived.status_code == 201
    assert revived.json()["openrag_document_id"] is None
    assert row is not None
    assert row.status is DocumentStatus.INDEXED
    assert row.openrag_document_id is None, "旧 id 不能活过删除"


async def test_a_replacement_upload_keeps_the_id_when_the_task_echoes_none() -> None:
    """反向守卫: 替换上传(非删除)时, 查不到新 id 必须保留旧 id —— 那是静默降级。"""
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        seeded_document_id="orag-still-valid",
    ) as (client, gateway):
        gateway.document_id = None
        response = await client.post("/api/documents/ingest", files=_upload("report.md"))

    assert response.status_code == 201
    assert response.json()["openrag_document_id"] == "orag-still-valid"


async def test_a_refused_delete_stays_busy_for_the_api_and_is_left_to_reconciliation() -> None:
    """被拒绝 → 行停 DELETING **且带原因**; 而 API 上的重试仍然是 409。

    这不是遗漏, 是已经拍过的原则: 重试是 P3 对账的职责, 不挂回请求路径 ——
    "把重试挂回请求路径, 等于让一个死掉的请求成为唯一的修复机会"(墓碑 404 那条决策)。
    代价写进了设计稿 §10.2: P3 的重试入口必须能作用于 DELETING 行。

    这条测试同时钉住两件事: 拒绝留下可发现的痕迹, 以及**状态没有移动**。
    """
    registry: dict[str, Any] = {}
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        tenant_role="developer",
        registry=registry,
    ) as (client, gateway):
        gateway.error = OpenRAGError("upstream 500", status_code=500)
        refused = await client.delete("/api/documents/report.md")

        async with registry["session_factory"]() as session:
            stuck = await SqlDocumentRepository(session).find_by_stored_filename(
                TenantId("t-acme"), "acme/report.md"
            )
        assert stuck is not None and stuck.status is DocumentStatus.DELETING
        assert stuck.status_reason is not None and "upstream 500" in stuck.status_reason

        # 再删一次: 名字仍然"忙"(DELETING) → 409。重试入口留给 P3 对账。
        gateway.error = None
        retried = await client.delete("/api/documents/report.md")

    assert refused.status_code == 502
    assert retried.status_code == 409
    assert "deleting" in retried.json()["detail"]
    assert gateway.deleted == [], "409 要在调 OpenRAG 之前就挡住"
