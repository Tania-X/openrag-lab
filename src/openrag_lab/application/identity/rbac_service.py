"""RBAC application service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.identity.services import merge_user_permissions
from openrag_lab.domain.shared.enums import GlobalRoleName
from openrag_lab.domain.shared.errors import PermissionDeniedError
from openrag_lab.domain.shared.ids import TenantId, UserId
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlGlobalRoleRepository,
    SqlPermissionRepository,
    SqlTenantUserRoleRepository,
    SqlUserGlobalRoleRepository,
    SqlUserRepository,
)


class RbacService:
    """Resolve and enforce permissions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._user_repo = SqlUserRepository(session)
        self._tenant_role_repo = SqlTenantUserRoleRepository(session)
        self._global_role_repo = SqlUserGlobalRoleRepository(session)
        self._global_role_def_repo = SqlGlobalRoleRepository(session)
        self._permission_repo = SqlPermissionRepository(session)

    async def effective_permissions(
        self, user_id: str, tenant_id: str
    ) -> set[str]:
        user = await self._user_repo.find_by_id(UserId(user_id))
        if user is None:
            return set()

        global_roles = await self._global_role_repo.global_roles_of_user(UserId(user_id))
        if any(r.name == GlobalRoleName.SUPER_ADMIN for r in global_roles):
            all_permissions = await self._permission_repo.list_all()
            return {p.name for p in all_permissions}

        tenant_roles = await self._tenant_role_repo.roles_of_user_in_tenant(
            TenantId(tenant_id), UserId(user_id)
        )
        return merge_user_permissions(user, tenant_roles, [])

    async def has_permission(self, user_id: str, tenant_id: str, permission: str) -> bool:
        permissions = await self.effective_permissions(user_id, tenant_id)
        return permission in permissions

    async def assert_permission(self, user_id: str, tenant_id: str, permission: str) -> None:
        if not await self.has_permission(user_id, tenant_id, permission):
            raise PermissionDeniedError(
                f"User {user_id} lacks permission {permission} in tenant {tenant_id}"
            )
