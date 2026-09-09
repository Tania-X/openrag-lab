import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.domain.identity.models import Tenant, TenantUserRole, User
from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.ids import TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlRoleRepository,
    SqlTenantRepository,
    SqlTenantUserRoleRepository,
    SqlUserGlobalRoleRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import seed_identity


@pytest.mark.asyncio
async def test_seed_and_repositories() -> None:
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

    async with session_factory() as session:
        tenant_repo = SqlTenantRepository(session)
        tenant = Tenant(id=TenantId("t1"), name="Acme", slug="acme")
        await tenant_repo.save(tenant)

        user_repo = SqlUserRepository(session)
        user = User(
            id=UserId("u1"),
            tenant_id=TenantId("t1"),
            username="alice",
            password_hash="hashed",
        )
        await user_repo.save(user)

        role_repo = SqlRoleRepository(session)
        user_role = await role_repo.find_by_name("user")
        assert user_role is not None

        tenant_user_role_repo = SqlTenantUserRoleRepository(session)
        await tenant_user_role_repo.assign_role(
            TenantUserRole(
                tenant_id=TenantId("t1"),
                user_id=UserId("u1"),
                role_id=user_role.id,
            )
        )

        roles = await tenant_user_role_repo.roles_of_user_in_tenant(
            TenantId("t1"), UserId("u1")
        )
        assert [r.name for r in roles] == ["user"]

        global_role_repo = SqlUserGlobalRoleRepository(session)
        # no global roles assigned by default
        assert await global_role_repo.global_roles_of_user(UserId("u1")) == []

        await session.commit()

    async with session_factory() as session:
        tenant_repo = SqlTenantRepository(session)
        saved = await tenant_repo.find_by_slug("acme")
        assert saved is not None
        assert saved.name == "Acme"
        assert saved.status is TenantStatus.ACTIVE

        user_repo = SqlUserRepository(session)
        saved_user = await user_repo.find_by_username("alice")
        assert saved_user is not None
        assert saved_user.status is UserStatus.ACTIVE

    await engine.dispose()
