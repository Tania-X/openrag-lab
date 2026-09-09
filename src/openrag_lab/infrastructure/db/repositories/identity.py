"""SQLAlchemy implementations of identity repositories."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.identity.models import (
    GlobalRole,
    Permission,
    Role,
    Tenant,
    TenantUserRole,
    User,
    UserGlobalRole,
)
from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.ids import GlobalRoleId, RoleId, TenantId, UserId
from openrag_lab.infrastructure.db.models.identity import (
    GlobalRoleModel,
    PermissionModel,
    RoleModel,
    TenantModel,
    UserModel,
    tenant_user_roles,
    user_global_roles,
)


def _tenant_to_domain(model: TenantModel) -> Tenant:
    return Tenant(
        id=TenantId(model.id),
        name=model.name,
        slug=model.slug,
        status=TenantStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _tenant_to_model(tenant: Tenant) -> TenantModel:
    return TenantModel(
        id=tenant.id.value,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


def _user_to_domain(model: UserModel) -> User:
    return User(
        id=UserId(model.id),
        tenant_id=TenantId(model.tenant_id),
        username=model.username,
        password_hash=model.password_hash,
        display_name=model.display_name,
        status=UserStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _user_to_model(user: User) -> UserModel:
    return UserModel(
        id=user.id.value,
        tenant_id=user.tenant_id.value,
        username=user.username,
        password_hash=user.password_hash,
        display_name=user.display_name,
        status=user.status.value,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _role_to_domain(model: RoleModel) -> Role:
    return Role(
        id=RoleId(model.id),
        name=model.name,
        description=model.description,
        is_system=model.is_system,
        permissions={p.name for p in model.permissions},
    )


def _role_to_model(role: Role) -> RoleModel:
    return RoleModel(
        id=role.id.value,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
    )


def _global_role_to_domain(model: GlobalRoleModel) -> GlobalRole:
    return GlobalRole(
        id=GlobalRoleId(model.id),
        name=model.name,
        description=model.description,
        is_system=model.is_system,
        permissions={p.name for p in model.permissions},
    )


def _global_role_to_model(role: GlobalRole) -> GlobalRoleModel:
    return GlobalRoleModel(
        id=role.id.value,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
    )


def _permission_to_domain(model: PermissionModel) -> Permission:
    return Permission(
        id=model.id,
        resource=model.resource,
        action=model.action,
    )


class SqlTenantRepository:
    """SQLAlchemy TenantRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_id(self, tenant_id: TenantId) -> Tenant | None:
        result = await self._session.get(TenantModel, tenant_id.value)
        return _tenant_to_domain(result) if result else None

    async def find_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(
            select(TenantModel).where(TenantModel.slug == slug)
        )
        model = result.scalar_one_or_none()
        return _tenant_to_domain(model) if model else None

    async def save(self, tenant: Tenant) -> None:
        model = await self._session.get(TenantModel, tenant.id.value)
        if model is None:
            self._session.add(_tenant_to_model(tenant))
        else:
            model.name = tenant.name
            model.slug = tenant.slug
            model.status = tenant.status.value
            model.updated_at = datetime.now(UTC)

    async def list_all(self) -> list[Tenant]:
        result = await self._session.execute(select(TenantModel).order_by(TenantModel.created_at))
        return [_tenant_to_domain(m) for m in result.scalars().all()]


class SqlUserRepository:
    """SQLAlchemy UserRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_id(self, user_id: UserId) -> User | None:
        result = await self._session.get(UserModel, user_id.value)
        return _user_to_domain(result) if result else None

    async def find_by_username(self, username: str) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.username == username)
        )
        model = result.scalar_one_or_none()
        return _user_to_domain(model) if model else None

    async def save(self, user: User) -> None:
        model = await self._session.get(UserModel, user.id.value)
        if model is None:
            self._session.add(_user_to_model(user))
        else:
            model.tenant_id = user.tenant_id.value
            model.username = user.username
            model.password_hash = user.password_hash
            model.display_name = user.display_name
            model.status = user.status.value
            model.updated_at = datetime.now(UTC)

    async def list_by_tenant(self, tenant_id: TenantId) -> list[User]:
        result = await self._session.execute(
            select(UserModel)
            .where(UserModel.tenant_id == tenant_id.value)
            .order_by(UserModel.created_at)
        )
        return [_user_to_domain(m) for m in result.scalars().all()]


class SqlRoleRepository:
    """SQLAlchemy RoleRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_id(self, role_id: RoleId) -> Role | None:
        result = await self._session.get(RoleModel, role_id.value)
        return _role_to_domain(result) if result else None

    async def find_by_name(self, name: str) -> Role | None:
        result = await self._session.execute(select(RoleModel).where(RoleModel.name == name))
        model = result.scalar_one_or_none()
        return _role_to_domain(model) if model else None

    async def list_all(self) -> list[Role]:
        result = await self._session.execute(select(RoleModel).order_by(RoleModel.name))
        return [_role_to_domain(m) for m in result.scalars().all()]

    async def save(self, role: Role) -> None:
        model = await self._session.get(RoleModel, role.id.value)
        if model is None:
            self._session.add(_role_to_model(role))
        else:
            model.name = role.name
            model.description = role.description
            model.is_system = role.is_system


class SqlGlobalRoleRepository:
    """SQLAlchemy GlobalRoleRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_id(self, role_id: GlobalRoleId) -> GlobalRole | None:
        result = await self._session.get(GlobalRoleModel, role_id.value)
        return _global_role_to_domain(result) if result else None

    async def find_by_name(self, name: str) -> GlobalRole | None:
        result = await self._session.execute(
            select(GlobalRoleModel).where(GlobalRoleModel.name == name)
        )
        model = result.scalar_one_or_none()
        return _global_role_to_domain(model) if model else None

    async def list_all(self) -> list[GlobalRole]:
        result = await self._session.execute(select(GlobalRoleModel).order_by(GlobalRoleModel.name))
        return [_global_role_to_domain(m) for m in result.scalars().all()]

    async def save(self, role: GlobalRole) -> None:
        model = await self._session.get(GlobalRoleModel, role.id.value)
        if model is None:
            self._session.add(_global_role_to_model(role))
        else:
            model.name = role.name
            model.description = role.description
            model.is_system = role.is_system


class SqlPermissionRepository:
    """SQLAlchemy PermissionRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Permission]:
        result = await self._session.execute(select(PermissionModel).order_by(PermissionModel.name))
        return [_permission_to_domain(m) for m in result.scalars().all()]

    async def list_by_names(self, names: set[str]) -> list[Permission]:
        result = await self._session.execute(
            select(PermissionModel).where(PermissionModel.name.in_(names))
        )
        return [_permission_to_domain(m) for m in result.scalars().all()]


class SqlTenantUserRoleRepository:
    """SQLAlchemy TenantUserRoleRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def roles_of_user_in_tenant(
        self, tenant_id: TenantId, user_id: UserId
    ) -> list[Role]:
        stmt = (
            select(RoleModel)
            .join(tenant_user_roles, tenant_user_roles.c.role_id == RoleModel.id)
            .where(
                tenant_user_roles.c.tenant_id == tenant_id.value,
                tenant_user_roles.c.user_id == user_id.value,
            )
        )
        result = await self._session.execute(stmt)
        return [_role_to_domain(m) for m in result.scalars().all()]

    async def assign_role(self, role: TenantUserRole) -> None:
        from openrag_lab.infrastructure.db.models.identity import tenant_user_roles as table

        await self._session.execute(
            table.insert().values(
                tenant_id=role.tenant_id.value,
                user_id=role.user_id.value,
                role_id=role.role_id.value,
                assigned_at=role.assigned_at,
            )
        )

    async def remove_role(
        self, tenant_id: TenantId, user_id: UserId, role_id: RoleId
    ) -> None:
        from openrag_lab.infrastructure.db.models.identity import tenant_user_roles as table

        await self._session.execute(
            delete(table).where(
                table.c.tenant_id == tenant_id.value,
                table.c.user_id == user_id.value,
                table.c.role_id == role_id.value,
            )
        )


class SqlUserGlobalRoleRepository:
    """SQLAlchemy UserGlobalRoleRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def global_roles_of_user(self, user_id: UserId) -> list[GlobalRole]:
        stmt = (
            select(GlobalRoleModel)
            .join(user_global_roles, user_global_roles.c.global_role_id == GlobalRoleModel.id)
            .where(user_global_roles.c.user_id == user_id.value)
        )
        result = await self._session.execute(stmt)
        return [_global_role_to_domain(m) for m in result.scalars().all()]

    async def assign_global_role(self, role: UserGlobalRole) -> None:
        from openrag_lab.infrastructure.db.models.identity import user_global_roles as table

        await self._session.execute(
            table.insert().values(
                user_id=role.user_id.value,
                global_role_id=role.global_role_id.value,
                assigned_at=role.assigned_at,
            )
        )
