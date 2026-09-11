"""Tests for tenant-scoped search and chat endpoints.

The point of these tests is the boundary: whatever the client sends, the
request that reaches OpenRAG must be scoped to exactly one tenant's registered
filenames, and the ``data_sources`` key must always be present.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.client import OpenRAGError
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Document, Tenant, TenantUserRole, User
from openrag_lab.domain.shared.enums import GlobalRoleName, TenantStatus
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlTenantRepository,
    SqlTenantUserRoleRepository,
    SqlUserGlobalRoleRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import seed_identity
from openrag_lab.infrastructure.db.session import get_session
from openrag_lab.interfaces.api.deps import CurrentUser, get_current_user, get_rag_gateway
from openrag_lab.interfaces.api.errors import register_exception_handlers
from openrag_lab.interfaces.api.routers import chat, search

TEST_API_KEY = "orag_test_key"
ACME_USER = CurrentUser(user_id="u-acme", tenant_id="t-acme", username="alice")
GLOBEX_USER = CurrentUser(user_id="u-globex", tenant_id="t-globex", username="bob")


@pytest.fixture(autouse=True)
def _pinned_openrag_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the shared key so the suite does not depend on a local .env."""
    monkeypatch.setattr(get_settings(), "openrag_api_key", TEST_API_KEY, raising=False)


class FakeGateway:
    """Records the calls the endpoints make instead of talking to OpenRAG."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: OpenRAGError | None = None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.calls.append(("search", kwargs))
        return {"results": [{"filename": "acme/report.md", "text": "hit", "score": 0.9}]}

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.calls.append(("chat", kwargs))
        return {"response": "answer"}

    @property
    def filters(self) -> dict[str, Any]:
        return self.calls[-1][1]["filters"]


async def _build_client(
    *,
    actor: CurrentUser | None,
    documents: list[tuple[str, str]] | None = None,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
    tenant_role: str | None = "user",
) -> tuple[AsyncClient, FakeGateway]:
    """Build the search/chat app over an in-memory DB.

    ``documents`` is a list of (tenant_id, display_name) owned by that tenant.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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

        for tenant_id, slug, username, status, global_role in (
            ("t-acme", "acme", "alice", tenant_status, None),
            ("t-globex", "globex", "bob", TenantStatus.ACTIVE, None),
        ):
            tenant = Tenant(id=TenantId(tenant_id), name=slug.title(), slug=slug, status=status)
            await tenant_repo.save(tenant)
            user = User(
                id=UserId(f"u-{slug}"),
                tenant_id=tenant.id,
                username=username,
                password_hash="hashed",
            )
            await user_repo.save(user)
            if tenant_role is not None:
                role = await _role(session, tenant_role)
                await role_repo.assign_role(
                    TenantUserRole(
                        tenant_id=tenant.id, user_id=user.id, role_id=role.id
                    )
                )
            if global_role is not None:
                await global_role_repo.assign_global_role(
                    _global_role(global_role, user.id)
                )
        await session.flush()

        # A super_admin lives in acme so cross-tenant requests can be tested.
        super_admin = User(
            id=UserId("u-root"),
            tenant_id=TenantId("t-acme"),
            username="root",
            password_hash="hashed",
        )
        await user_repo.save(super_admin)
        await global_role_repo.assign_global_role(
            _global_role(GlobalRoleName.SUPER_ADMIN.value, super_admin.id)
        )

        for tenant_id, display_name in documents or []:
            tenant = await tenant_repo.find_by_id(TenantId(tenant_id))
            assert tenant is not None
            stored = tenant.scope_filename(display_name)
            await document_repo.save(
                Document(
                    id=DocumentId(stored),
                    tenant_id=tenant.id,
                    stored_filename=stored,
                    display_name=display_name,
                    uploaded_by=UserId(f"u-{tenant.slug}"),
                )
            )
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(search.router)
    app.include_router(chat.router)

    gateway = FakeGateway()

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_rag_gateway] = lambda: gateway
    if actor is not None:
        app.dependency_overrides[get_current_user] = lambda: actor

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, gateway


async def _role(session: AsyncSession, name: str):
    from openrag_lab.infrastructure.db.repositories.identity import SqlRoleRepository

    role = await SqlRoleRepository(session).find_by_name(name)
    assert role is not None
    return role


def _global_role(name: str, user_id: UserId):
    from openrag_lab.domain.identity.models import UserGlobalRole
    from openrag_lab.domain.shared.ids import GlobalRoleId

    return UserGlobalRole(
        user_id=user_id,
        global_role_id=GlobalRoleId(f"global-role-{name}"),
    )


async def test_search_requires_authentication() -> None:
    client, _ = await _build_client(actor=None)
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 401


async def test_search_requires_the_search_permission() -> None:
    client, _ = await _build_client(actor=ACME_USER, tenant_role=None)
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 403


async def test_search_is_scoped_to_the_callers_registered_documents() -> None:
    client, gateway = await _build_client(
        actor=ACME_USER,
        documents=[("t-acme", "report.md"), ("t-globex", "secret.md")],
    )
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 200
    assert gateway.filters == {"data_sources": ["acme/report.md"]}
    # The shared key comes from settings, never from the request.
    assert gateway.calls[-1][1]["api_key"] == TEST_API_KEY
    assert response.json()["scope"] == {
        "tenant_id": "t-acme",
        "document_count": 1,
        "cross_tenant": False,
    }


async def test_tenant_without_documents_still_sends_an_empty_scope() -> None:
    """An empty list means "match nothing"; omitting the key would mean "all"."""
    client, gateway = await _build_client(actor=ACME_USER, documents=[])
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 200
    assert gateway.filters == {"data_sources": []}
    assert response.json()["scope"]["document_count"] == 0


async def test_client_cannot_inject_filters() -> None:
    """A caller cannot widen the boundary by sending its own filters."""
    client, gateway = await _build_client(
        actor=ACME_USER, documents=[("t-acme", "report.md")]
    )
    async with client:
        response = await client.post(
            "/api/search",
            json={
                "query": "报表",
                "filters": {"data_sources": ["globex/secret.md"], "owners": ["anonymous"]},
            },
        )
    assert response.status_code == 422
    assert gateway.calls == []


async def test_cross_tenant_tenant_id_is_rejected_for_non_super_admin() -> None:
    client, gateway = await _build_client(
        actor=ACME_USER, documents=[("t-globex", "secret.md")]
    )
    async with client:
        response = await client.post(
            "/api/search", json={"query": "报表", "tenant_id": "t-globex"}
        )
    assert response.status_code == 403
    assert gateway.calls == []


async def test_super_admin_can_scope_to_another_tenant() -> None:
    root = CurrentUser(user_id="u-root", tenant_id="t-acme", username="root")
    client, gateway = await _build_client(
        actor=root,
        documents=[("t-acme", "report.md"), ("t-globex", "secret.md")],
    )
    async with client:
        response = await client.post(
            "/api/search", json={"query": "报表", "tenant_id": "t-globex"}
        )
    assert response.status_code == 200
    assert gateway.filters == {"data_sources": ["globex/secret.md"]}
    assert response.json()["scope"] == {
        "tenant_id": "t-globex",
        "document_count": 1,
        "cross_tenant": True,
    }


async def test_super_admin_defaults_to_their_own_tenant() -> None:
    root = CurrentUser(user_id="u-root", tenant_id="t-acme", username="root")
    client, gateway = await _build_client(
        actor=root,
        documents=[("t-acme", "report.md"), ("t-globex", "secret.md")],
    )
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 200
    assert gateway.filters == {"data_sources": ["acme/report.md"]}
    assert response.json()["scope"]["cross_tenant"] is False


async def test_disabled_tenant_cannot_be_queried() -> None:
    client, gateway = await _build_client(
        actor=ACME_USER, tenant_status=TenantStatus.DISABLED
    )
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 403
    assert gateway.calls == []


async def test_chat_uses_the_same_boundary() -> None:
    client, gateway = await _build_client(
        actor=ACME_USER,
        documents=[("t-acme", "report.md"), ("t-globex", "secret.md")],
    )
    async with client:
        response = await client.post("/api/chat", json={"message": "投诉时限?"})
    assert response.status_code == 200
    assert gateway.calls[-1][0] == "chat"
    assert gateway.filters == {"data_sources": ["acme/report.md"]}
    assert response.json() == {
        "response": "answer",
        "scope": {"tenant_id": "t-acme", "document_count": 1, "cross_tenant": False},
    }


async def test_chat_requires_the_chat_permission() -> None:
    client, _ = await _build_client(actor=ACME_USER, tenant_role=None)
    async with client:
        response = await client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 403


async def test_openrag_failure_is_reported_as_bad_gateway() -> None:
    client, gateway = await _build_client(actor=ACME_USER)
    gateway.error = OpenRAGError("boom")
    async with client:
        response = await client.post("/api/search", json={"query": "报表"})
    assert response.status_code == 502


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "报表", "limit": 0},
        {"query": "报表", "limit": 999},
        {"query": "报表", "score_threshold": 2.0},
    ],
)
async def test_invalid_search_payloads_are_rejected(payload: dict[str, Any]) -> None:
    client, _ = await _build_client(actor=ACME_USER)
    async with client:
        response = await client.post("/api/search", json=payload)
    assert response.status_code == 422
