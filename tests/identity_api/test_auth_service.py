import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.application.identity.auth_service import AuthService
from openrag_lab.application.identity.user_service import UserService
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.seed import seed_identity


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
        token = await service.login("admin", "admin123")
        assert token["username"] == "admin"

    async with session_factory() as session:
        service = AuthService(session)
        result = await service.register("alice", "secret123", tenant_name="Alice Corp")
        assert result["username"] == "alice"
        assert result["tenant_id"]

    async with session_factory() as session:
        service = AuthService(session)
        me = await service.me(result["user_id"])
        assert "users:write" in me["permissions"]  # tenant_admin
        assert "tenants:read" in me["permissions"]

    async with session_factory() as session:
        service = UserService(session)
        user = await service.create_user(
            username="bob",
            password="bobpass",
            tenant_id=result["tenant_id"],
            role_name="viewer",
        )
        assert user.username == "bob"

    await engine.dispose()
