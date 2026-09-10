"""User management application service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.identity.models import TenantUserRole, User
from openrag_lab.domain.shared.enums import TenantRoleName
from openrag_lab.domain.shared.errors import AlreadyExistsError, NotFoundError
from openrag_lab.domain.shared.ids import TenantId, UserId
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlRoleRepository,
    SqlTenantRepository,
    SqlTenantUserRoleRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import TENANT_ROLES
from openrag_lab.infrastructure.security.password import hash_password


class UserService:
    """Administrative user operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._user_repo = SqlUserRepository(session)
        self._tenant_repo = SqlTenantRepository(session)
        self._role_repo = SqlRoleRepository(session)
        self._tenant_user_role_repo = SqlTenantUserRoleRepository(session)

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        tenant_id: str,
        role_name: TenantRoleName = TenantRoleName.USER,
        display_name: str | None = None,
    ) -> User:
        if await self._user_repo.find_by_username(username) is not None:
            raise AlreadyExistsError(f"Username already exists: {username}")

        tenant = await self._tenant_repo.find_by_id(TenantId(tenant_id))
        if tenant is None:
            raise NotFoundError(f"Tenant not found: {tenant_id}")

        if not isinstance(role_name, TenantRoleName):
            try:
                role_name = TenantRoleName(role_name)
            except ValueError as exc:
                raise NotFoundError(f"Tenant role not found: {role_name}") from exc
        if role_name not in TENANT_ROLES:
            raise NotFoundError(f"Tenant role not found: {role_name.value}")
        role = await self._role_repo.find_by_name(role_name.value)
        if role is None:
            raise NotFoundError(f"Role not found: {role_name.value}")

        user = User(
            id=UserId.generate(),
            tenant_id=tenant.id,
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
        )
        await self._user_repo.save(user)
        await self._tenant_user_role_repo.assign_role(
            TenantUserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id)
        )
        await self._session.flush()
        return user

    async def list_users(self, tenant_id: str) -> list[User]:
        return await self._user_repo.list_by_tenant(TenantId(tenant_id))

    async def get_user(self, user_id: str) -> User:
        user = await self._user_repo.find_by_id(UserId(user_id))
        if user is None:
            raise NotFoundError("User not found")
        return user
