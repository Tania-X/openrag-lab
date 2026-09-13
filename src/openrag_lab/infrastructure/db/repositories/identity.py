"""SQLAlchemy implementations of identity repositories."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.identity.models import (
    Document,
    GlobalRole,
    Permission,
    Role,
    Tenant,
    TenantUserRole,
    User,
    UserGlobalRole,
)
from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import AlreadyExistsError, InvalidOperationError
from openrag_lab.domain.shared.ids import (
    DocumentId,
    GlobalRoleId,
    RoleId,
    TenantId,
    UserId,
)
from openrag_lab.infrastructure.db.models.identity import (
    DocumentModel,
    GlobalRoleModel,
    PermissionModel,
    RoleModel,
    TenantModel,
    UserModel,
    tenant_user_roles,
    user_global_roles,
)


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite does not preserve tzinfo; treat naive DB timestamps as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _coerce_tenant_status(value: TenantStatus | str) -> TenantStatus:
    if isinstance(value, TenantStatus):
        return value
    return TenantStatus(value)


def _coerce_user_status(value: UserStatus | str) -> UserStatus:
    if isinstance(value, UserStatus):
        return value
    return UserStatus(value)


def _tenant_to_domain(model: TenantModel) -> Tenant:
    try:
        return Tenant(
            id=TenantId(model.id),
            name=model.name,
            slug=model.slug,
            status=_coerce_tenant_status(model.status),
            created_at=_ensure_utc(model.created_at),
            updated_at=_ensure_utc(model.updated_at),
        )
    except InvalidOperationError as exc:
        # A stored row that violates the slug invariant cannot be served: the
        # slug is the tenant's document namespace, so serving it would break
        # isolation. Fail with a diagnosable message instead of a bare error.
        raise InvalidOperationError(
            f"Tenant row {model.id} is unusable: {exc}. "
            "Repair or remove the row (its slug must not contain whitespace or "
            "a path separator; see docs/rbac-tenant-ddd-design.md §11)."
        ) from exc


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
        status=_coerce_user_status(model.status),
        created_at=_ensure_utc(model.created_at),
        updated_at=_ensure_utc(model.updated_at),
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


async def _resolve_permission_models(
    session: AsyncSession, names: set[str]
) -> list[PermissionModel]:
    if not names:
        return []
    result = await session.execute(
        select(PermissionModel).where(PermissionModel.name.in_(names))
    )
    return list(result.scalars().all())


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
            model.status = tenant.status
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
            model.status = user.status
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
        permissions = await _resolve_permission_models(self._session, role.permissions)
        missing = role.permissions - {p.name for p in permissions}
        if missing:
            raise ValueError(f"Unknown permissions for role {role.name}: {sorted(missing)}")
        if model is None:
            model = _role_to_model(role)
            model.permissions = permissions
            self._session.add(model)
        else:
            model.name = role.name
            model.description = role.description
            model.is_system = role.is_system
            model.permissions = permissions


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
        permissions = await _resolve_permission_models(self._session, role.permissions)
        missing = role.permissions - {p.name for p in permissions}
        if missing:
            raise ValueError(
                f"Unknown permissions for global role {role.name}: {sorted(missing)}"
            )
        if model is None:
            model = _global_role_to_model(role)
            model.permissions = permissions
            self._session.add(model)
        else:
            model.name = role.name
            model.description = role.description
            model.is_system = role.is_system
            model.permissions = permissions


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

        existing = await self._session.execute(
            select(table).where(
                table.c.tenant_id == role.tenant_id.value,
                table.c.user_id == role.user_id.value,
                table.c.role_id == role.role_id.value,
            )
        )
        if existing.first() is not None:
            return
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

        existing = await self._session.execute(
            select(table).where(
                table.c.user_id == role.user_id.value,
                table.c.global_role_id == role.global_role_id.value,
            )
        )
        if existing.first() is not None:
            return
        await self._session.execute(
            table.insert().values(
                user_id=role.user_id.value,
                global_role_id=role.global_role_id.value,
                assigned_at=role.assigned_at,
            )
        )


def _document_to_domain(model: DocumentModel) -> Document:
    return Document(
        id=DocumentId(model.id),
        tenant_id=TenantId(model.tenant_id),
        stored_filename=model.stored_filename,
        display_name=model.display_name,
        uploaded_by=UserId(model.uploaded_by),
        mimetype=model.mimetype,
        size_bytes=model.size_bytes,
        openrag_document_id=model.openrag_document_id,
        created_at=_ensure_utc(model.created_at),
        updated_at=_ensure_utc(model.updated_at),
    )


def _document_to_model(document: Document) -> DocumentModel:
    return DocumentModel(
        id=document.id.value,
        tenant_id=document.tenant_id.value,
        stored_filename=document.stored_filename,
        display_name=document.display_name,
        uploaded_by=document.uploaded_by.value,
        mimetype=document.mimetype,
        size_bytes=document.size_bytes,
        openrag_document_id=document.openrag_document_id,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


class SqlDocumentRepository:
    """SQLAlchemy DocumentRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, document: Document) -> None:
        model = await self._session.get(DocumentModel, document.id.value)
        clash = await self._session.execute(
            select(DocumentModel.id).where(
                DocumentModel.tenant_id == document.tenant_id.value,
                DocumentModel.stored_filename == document.stored_filename,
            )
        )
        owner_id = clash.scalar_one_or_none()
        if owner_id is not None and owner_id != document.id.value:
            # (tenant_id, stored_filename) is the registry's key: report the
            # clash as a domain error so callers get a 4xx instead of an
            # IntegrityError that rolls the whole transaction back at commit.
            raise AlreadyExistsError(
                f"Document already registered for tenant {document.tenant_id.value}: "
                f"{document.stored_filename}"
            )
        if model is None:
            self._session.add(_document_to_model(document))
            return
        model.tenant_id = document.tenant_id.value
        model.stored_filename = document.stored_filename
        model.display_name = document.display_name
        model.uploaded_by = document.uploaded_by.value
        model.mimetype = document.mimetype
        model.size_bytes = document.size_bytes
        model.openrag_document_id = document.openrag_document_id
        model.updated_at = document.updated_at

    async def find_by_id(self, document_id: DocumentId) -> Document | None:
        model = await self._session.get(DocumentModel, document_id.value)
        return _document_to_domain(model) if model else None

    async def find_by_stored_filename(
        self, tenant_id: TenantId, stored_filename: str
    ) -> Document | None:
        result = await self._session.execute(
            select(DocumentModel).where(
                DocumentModel.tenant_id == tenant_id.value,
                DocumentModel.stored_filename == stored_filename,
            )
        )
        model = result.scalar_one_or_none()
        return _document_to_domain(model) if model else None

    async def list_by_tenant(self, tenant_id: TenantId) -> list[Document]:
        result = await self._session.execute(
            select(DocumentModel)
            .where(DocumentModel.tenant_id == tenant_id.value)
            .order_by(DocumentModel.created_at)
        )
        return [_document_to_domain(m) for m in result.scalars().all()]

    async def list_stored_filenames(self, tenant_id: TenantId) -> list[str]:
        result = await self._session.execute(
            select(DocumentModel.stored_filename)
            .where(DocumentModel.tenant_id == tenant_id.value)
            .order_by(DocumentModel.created_at)
        )
        return list(result.scalars().all())

    async def delete(self, document_id: DocumentId) -> None:
        await self._session.execute(
            delete(DocumentModel).where(DocumentModel.id == document_id.value)
        )
