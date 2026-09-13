import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.application.identity.auth_service import AuthService
from openrag_lab.application.identity.user_service import UserService
from openrag_lab.config import get_settings
from openrag_lab.domain.shared.enums import TenantRoleName
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.seed import TENANT_ROLES, seed_identity


@pytest.mark.asyncio
async def test_register_login_and_admin_create_user() -> None:
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
        await session.commit()
        await AuthService(session).ensure_bootstrap_admin()
        await session.commit()

    async with session_factory() as session:
        service = AuthService(session)
        settings = get_settings()
        token = await service.login(
            settings.bootstrap_admin_username,
            settings.bootstrap_admin_password,
        )
        assert token["username"] == settings.bootstrap_admin_username
        admin_me = await service.me(token["user_id"], token["tenant_id"])
        assert "tenants:write" in admin_me["permissions"]
        # super_admin is a *global* role and holds the whole permission catalog,
        # so per-tenant permissions like search:use/chat:use are included even
        # though the bootstrap admin has a tenant role only in `default`.
        # Pinned here because a review read the RAG route check passing for
        # super_admin as "the permission check is bypassed".
        assert {"search:use", "chat:use"} <= set(admin_me["permissions"])

    async with session_factory() as session:
        service = AuthService(session)
        result = await service.register("alice", "secret123", tenant_name="Alice Corp")
        assert result["username"] == "alice"
        assert result["tenant_id"]

    async with session_factory() as session:
        service = AuthService(session)
        me = await service.me(result["user_id"], result["tenant_id"])
        permissions = set(me["permissions"])
        assert "users:write" in permissions
        assert "tenants:write" not in permissions
        assert TENANT_ROLES[TenantRoleName.TENANT_ADMIN] <= permissions

    async with session_factory() as session:
        user_service = UserService(session)
        user = await user_service.create_user(
            username="bob",
            password="bobpass",
            tenant_id=result["tenant_id"],
            role_name=TenantRoleName.VIEWER,
        )
        assert user.username == "bob"

    await engine.dispose()
