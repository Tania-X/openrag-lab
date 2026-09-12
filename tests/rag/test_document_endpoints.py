"""Tests for tenant-scoped document management.

Same boundary discipline as retrieval: whatever the client sends, the request
that reaches OpenRAG must be scoped to one tenant, and the registry must never
claim a document that is not actually indexed.
"""

from __future__ import annotations

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
from openrag_lab.domain.shared.enums import GlobalRoleName
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
ACME_TENANT = CurrentUser(user_id="u-acme", tenant_id="t-acme", username="alice")
GLOBEX_TENANT = CurrentUser(user_id="u-globex", tenant_id="t-globex", username="bob")
ROOT = CurrentUser(user_id="u-root", tenant_id="t-acme", username="root")


@pytest.fixture(autouse=True)
def _pinned_openrag_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "openrag_api_key", TEST_API_KEY, raising=False)


class FakeDocumentGateway:
    """Records ingest/delete calls and can be told to fail."""

    def __init__(self) -> None:
        self.ingested: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self.task: dict[str, Any] = {"status": "completed", "failed_files": 0}
        self.error: OpenRAGError | None = None
        self.staged_path: Path | None = None

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
        return {"success": True, "deleted_chunks": 3}


@asynccontextmanager
async def _build_client(
    *,
    actor: CurrentUser | None,
    documents_seed: list[tuple[str, str]] | None = None,
    tenant_role: str | None = "user",
):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            await seed_identity(session)
            tenant_repo = SqlTenantRepository(session)
            user_repo = SqlUserRepository(session)
            role_repo = SqlTenantUserRoleRepository(session)
            global_role_repo = SqlUserGlobalRoleRepository(session)
            document_repo = SqlDocumentRepository(session)
            role = await SqlRoleRepository(session).find_by_name(tenant_role or "user")

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
                tenant = await tenant_repo.find_by_id(TenantId(tid))
                assert tenant is not None
                stored = tenant.scope_filename(display_name)
                from openrag_lab.domain.identity.models import Document
                from openrag_lab.domain.shared.ids import DocumentId

                await document_repo.save(
                    Document(
                        id=DocumentId(stored),
                        tenant_id=tenant.id,
                        stored_filename=stored,
                        display_name=display_name,
                        uploaded_by=UserId(f"u-{tenant.slug}"),
                        mimetype="text/markdown",
                        size_bytes=10,
                    )
                )
            await session.commit()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(documents.router)

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


async def test_listing_requires_the_read_permission() -> None:
    async with _build_client(actor=ACME_TENANT, tenant_role="viewer") as (client, _):
        response = await client.get("/api/documents")
    # viewer has documents:read
    assert response.status_code == 200


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
        response = await client.post("/api/documents/ingest", files=_upload("report.md"))
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 201
    assert listing["total"] == 1


async def test_failed_ingestion_leaves_no_registry_entry() -> None:
    async with _build_client(actor=ACME_TENANT) as (client, gateway):
        gateway.task = {"status": "failed", "failed_files": 1, "error": "unsupported"}
        response = await client.post("/api/documents/ingest", files=_upload("bad.bin"))
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 400
    assert "did not complete" in response.json()["detail"]
    assert listing["total"] == 0


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


async def test_delete_removes_from_openrag_and_the_registry() -> None:
    async with _build_client(
        actor=ACME_TENANT,
        documents_seed=[("t-acme", "report.md")],
        tenant_role="developer",
    ) as (client, gateway):
        response = await client.delete("/api/documents/report.md")
        listing = (await client.get("/api/documents")).json()
    assert response.status_code == 200
    assert response.json() == {
        "filename": "report.md",
        "stored_filename": "acme/report.md",
        "deleted_chunks": 3,
    }
    assert gateway.deleted[-1]["stored_filename"] == "acme/report.md"
    assert listing["total"] == 0


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
