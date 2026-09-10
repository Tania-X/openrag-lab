"""Auth application service."""

from __future__ import annotations

import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.application.identity.rbac_service import RbacService
from openrag_lab.application.identity.tenant_service import TenantService, slugify
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import TenantUserRole, User, UserGlobalRole
from openrag_lab.domain.shared.enums import GlobalRoleName, TenantRoleName, UserStatus
from openrag_lab.domain.shared.errors import (
    AlreadyExistsError,
    InvalidOperationError,
    NotFoundError,
)
from openrag_lab.domain.shared.ids import UserId
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlGlobalRoleRepository,
    SqlRoleRepository,
    SqlTenantUserRoleRepository,
    SqlUserGlobalRoleRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.security.jwt import create_access_token
from openrag_lab.infrastructure.security.password import hash_password, verify_password


class AuthService:
    """Register, login, and bootstrap admin."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._user_repo = SqlUserRepository(session)
        self._tenant_service = TenantService(session)
        self._role_repo = SqlRoleRepository(session)
        self._tenant_user_role_repo = SqlTenantUserRoleRepository(session)
        self._global_role_repo = SqlGlobalRoleRepository(session)
        self._user_global_role_repo = SqlUserGlobalRoleRepository(session)
        self._rbac = RbacService(session)

    async def register(
        self,
        username: str,
        password: str,
        display_name: str | None = None,
        tenant_name: str | None = None,
    ) -> dict:
        if len(password) < 8:
            raise InvalidOperationError("Password must be at least 8 characters")
        existing_user = await self._user_repo.find_by_username(username)
        if existing_user is not None:
            raise AlreadyExistsError(f"Username already exists: {username}")

        tenant_name = tenant_name or username
        base_slug = slugify(tenant_name)
        tenant_slug = base_slug
        for _ in range(10):
            if await self._tenant_service.get_tenant_by_slug(tenant_slug) is None:
                break
            tenant_slug = f"{base_slug}-{secrets.token_hex(6)}"
        else:
            raise InvalidOperationError(
                f"Could not allocate a unique tenant slug for: {tenant_name}"
            )

        try:
            tenant = await self._tenant_service.create_tenant(tenant_name, tenant_slug)
            user = User(
                id=UserId.generate(),
                tenant_id=tenant.id,
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
            )
            await self._user_repo.save(user)

            tenant_admin_role = await self._role_repo.find_by_name(TenantRoleName.TENANT_ADMIN)
            if tenant_admin_role is None:
                raise NotFoundError("Built-in tenant_admin role not found")
            await self._tenant_user_role_repo.assign_role(
                TenantUserRole(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role_id=tenant_admin_role.id,
                )
            )

            await self._session.commit()
            return self._token_response(user)
        except Exception:
            await self._session.rollback()
            raise

    async def login(self, username: str, password: str) -> dict:
        user = await self._user_repo.find_by_username(username)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidOperationError("Invalid username or password")
        if user.status != UserStatus.ACTIVE:
            raise InvalidOperationError("User is disabled")
        return self._token_response(user)

    async def me(self, user_id: str, tenant_id: str) -> dict:
        user = await self._user_repo.find_by_id(UserId(user_id))
        if user is None:
            raise NotFoundError("User not found")
        if user.tenant_id.value != tenant_id:
            raise NotFoundError("User not found")
        if user.status != UserStatus.ACTIVE:
            raise InvalidOperationError("User is disabled")
        permissions = await self._rbac.effective_permissions(user.id.value, tenant_id)
        return {
            "user_id": user.id.value,
            "tenant_id": user.tenant_id.value,
            "username": user.username,
            "display_name": user.display_name,
            "permissions": sorted(permissions),
        }

    async def ensure_bootstrap_admin(self) -> None:
        settings = get_settings()
        existing = await self._user_repo.find_by_username(settings.bootstrap_admin_username)
        if existing is not None:
            return

        try:
            tenant = await self._tenant_service.get_tenant_by_slug("default")
            if tenant is None:
                tenant = await self._tenant_service.create_tenant("Default", "default")

            user = User(
                id=UserId.generate(),
                tenant_id=tenant.id,
                username=settings.bootstrap_admin_username,
                password_hash=hash_password(settings.bootstrap_admin_password),
                display_name="Bootstrap Admin",
            )
            await self._user_repo.save(user)

            tenant_admin_role = await self._role_repo.find_by_name(TenantRoleName.TENANT_ADMIN)
            if tenant_admin_role is not None:
                await self._tenant_user_role_repo.assign_role(
                    TenantUserRole(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        role_id=tenant_admin_role.id,
                    )
                )

            super_admin_role = await self._global_role_repo.find_by_name(GlobalRoleName.SUPER_ADMIN)
            if super_admin_role is not None:
                await self._user_global_role_repo.assign_global_role(
                    UserGlobalRole(user_id=user.id, global_role_id=super_admin_role.id)
                )

            await self._session.commit()
        except IntegrityError:
            # Concurrent bootstrap race: another worker created the admin first.
            await self._session.rollback()

    def _token_response(self, user: User) -> dict:
        token = create_access_token(
            user_id=user.id.value,
            tenant_id=user.tenant_id.value,
            username=user.username,
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user.id.value,
            "tenant_id": user.tenant_id.value,
            "username": user.username,
        }
