"""Endpoint-level tests for the authentication chain (`/api/auth/me`).

Why this file exists: `test_auth_service.py` calls `AuthService.me(...)` directly,
so it cannot catch a mistake in the *wiring* — the router passing the wrong
identity fields, or `get_current_user` failing to attach the bearer challenge to
its 401. These tests run 门0→门3 for real: HTTPBearer, JWT decoding, the
user/tenant status checks, the dependency graph and the router.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openrag_lab.application.identity.auth_service import AuthService
from openrag_lab.config import get_settings
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import SqlUserRepository
from openrag_lab.infrastructure.db.seed import seed_identity
from openrag_lab.infrastructure.db.session import get_session
from openrag_lab.interfaces.api.deps import CurrentUser, require_permission
from openrag_lab.interfaces.api.errors import register_exception_handlers
from openrag_lab.interfaces.api.routers import auth


@asynccontextmanager
async def _app_with_admin(tmp_path: Path):
    """An app whose only router is `auth`, backed by a seeded database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await seed_identity(session)
            await session.commit()
            await AuthService(session).ensure_bootstrap_admin()
            await session.commit()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(auth.router)

        async def _session_override():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = _session_override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        await engine.dispose()


async def _admin_session_and_token(factory):
    """Log the bootstrap admin in; return (session-bound service, token response)."""
    settings = get_settings()
    session = factory()
    token = await AuthService(session).login(
        settings.bootstrap_admin_username, settings.bootstrap_admin_password
    )
    return session, token


async def test_me_returns_the_identity_get_current_user_established(tmp_path: Path) -> None:
    """完整链路: 门0(HTTPBearer) → 门1(JWT + 用户/租户状态) → 端点。

    `display_name` 只有在 deps 把 user 行上的值传下来时才会出现 —— 这正是本文件
    要守的那根接线。
    """
    settings = get_settings()
    async with _app_with_admin(tmp_path) as (client, factory):
        async with factory() as session:
            token = await AuthService(session).login(
                settings.bootstrap_admin_username, settings.bootstrap_admin_password
            )
        response = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == token["user_id"]
    assert body["tenant_id"] == token["tenant_id"]
    assert body["username"] == token["username"]
    assert body["display_name"] == "Bootstrap Admin"  # 来自 deps 传递, 不是端点自己查库
    assert "tenants:write" in body["permissions"]


async def test_missing_token_answers_401_with_the_bearer_challenge(tmp_path: Path) -> None:
    """RFC 7235: 401 必须带 WWW-Authenticate 挑战头。

    我们为了自己掌控响应体用了 `HTTPBearer(auto_error=False)`, 于是也接管了框架
    本会替我们发的这个头 —— 这条测试把那笔义务钉住。
    """
    async with _app_with_admin(tmp_path) as (client, _):
        response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


async def test_invalid_token_also_carries_the_challenge(tmp_path: Path) -> None:
    """反向守卫: 不只是"没带头"那条路径要带挑战头, 坏 token 也要。"""
    async with _app_with_admin(tmp_path) as (client, _):
        response = await client.get(
            "/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
        )

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


async def test_permission_denial_is_generic_but_logged(tmp_path: Path, caplog) -> None:
    """403 响应不回显权限名, 但日志里必须有 —— 否则 403 无法排查。"""
    async with _app_with_admin(tmp_path) as (_, factory):
        async with factory() as session:
            # 不存在的权限名 → assert_permission 必然拒绝(与角色无关)
            dependency = require_permission("nonexistent:permission")
            actor = CurrentUser(
                user_id="u-nobody",
                tenant_id="t-none",
                username="nobody",
                display_name="Nobody",
            )
            with caplog.at_level(logging.WARNING, logger="openrag_lab.interfaces.api.deps"):
                with pytest.raises(HTTPException) as excinfo:
                    await dependency(current_user=actor, session=session)

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "permission_denied"      # 响应笼统
    assert "permission denied" in caplog.text                # 日志具体
    assert "nonexistent:permission" in caplog.text


async def test_me_queries_the_user_row_only_through_rbac(tmp_path: Path, monkeypatch) -> None:
    """item 5 回归: `/me` 不再重复查 user 行(deps 查一次、me 又查一次)。

    用**计数**而不是"禁止调用": RBAC 的 `effective_permissions` 内部仍需要 user 行,
    所以正确的不变量是"一次", 不是"零次"。
    """
    settings = get_settings()
    async with _app_with_admin(tmp_path) as (_, factory):
        calls: list[str] = []
        original = SqlUserRepository.find_by_id

        async def counting_find_by_id(self, user_id):
            calls.append(user_id.value)
            return await original(self, user_id)

        monkeypatch.setattr(SqlUserRepository, "find_by_id", counting_find_by_id)

        async with factory() as session:
            user = await SqlUserRepository(session).find_by_username(
                settings.bootstrap_admin_username
            )
            assert user is not None
            calls.clear()  # 只统计 me() 这一次调用
            me = await AuthService(session).me(
                user_id=user.id.value,
                tenant_id=user.tenant_id.value,
                username=user.username,
                display_name=user.display_name or "",
            )

    assert me["display_name"] == "Bootstrap Admin"
    assert len(calls) == 1, f"user 行只应由 RBAC 查一次, 实际 {len(calls)} 次"
