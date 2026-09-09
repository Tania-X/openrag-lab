"""Identity and access domain models.

These are pure Python domain objects. They must not import FastAPI,
SQLAlchemy, or Pydantic. Persistence is handled by infrastructure
repositories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import InvalidOperationError
from openrag_lab.domain.shared.ids import GlobalRoleId, RoleId, TenantId, UserId


@dataclass(slots=True)
class Permission:
    """A single permission such as ``search:use``."""

    id: str
    resource: str
    action: str

    @property
    def name(self) -> str:
        return f"{self.resource}:{self.action}"


@dataclass(slots=True)
class Role:
    """A tenant-level role assigned to users inside a tenant."""

    id: RoleId
    name: str
    description: str = ""
    is_system: bool = False
    permissions: set[str] = field(default_factory=set)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(slots=True)
class GlobalRole:
    """A platform-level role that spans all tenants."""

    id: GlobalRoleId
    name: str
    description: str = ""
    is_system: bool = False
    permissions: set[str] = field(default_factory=set)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(slots=True)
class Tenant:
    """Tenant aggregate root."""

    id: TenantId
    name: str
    slug: str
    status: TenantStatus = TenantStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def activate(self) -> None:
        if self.status is TenantStatus.ACTIVE:
            return
        self.status = TenantStatus.ACTIVE
        self.updated_at = datetime.now(UTC)

    def disable(self) -> None:
        if self.status is TenantStatus.DISABLED:
            return
        self.status = TenantStatus.DISABLED
        self.updated_at = datetime.now(UTC)

    def ensure_active(self) -> None:
        if self.status is not TenantStatus.ACTIVE:
            raise InvalidOperationError(f"Tenant {self.slug} is not active")


@dataclass(slots=True)
class User:
    """User aggregate root.

    Phase 1: a user belongs to exactly one tenant.
    """

    id: UserId
    tenant_id: TenantId
    username: str
    password_hash: str
    display_name: str | None = None
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def change_password_hash(self, new_password_hash: str) -> None:
        if not new_password_hash:
            raise InvalidOperationError("password hash must not be empty")
        self.password_hash = new_password_hash
        self.updated_at = datetime.now(UTC)

    def activate(self) -> None:
        if self.status is UserStatus.ACTIVE:
            return
        self.status = UserStatus.ACTIVE
        self.updated_at = datetime.now(UTC)

    def disable(self) -> None:
        if self.status is UserStatus.DISABLED:
            return
        self.status = UserStatus.DISABLED
        self.updated_at = datetime.now(UTC)

    def ensure_active(self) -> None:
        if self.status is not UserStatus.ACTIVE:
            raise InvalidOperationError(f"User {self.username} is not active")


@dataclass(frozen=True, slots=True)
class TenantUserRole:
    """Assignment of a role to a user within a tenant.

    Phase 1 allows at most one role per user per tenant, but the
    structure can hold multiple rows if that constraint is relaxed.
    """

    tenant_id: TenantId
    user_id: UserId
    role_id: RoleId
    assigned_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class UserGlobalRole:
    """Assignment of a platform-level role to a user."""

    user_id: UserId
    global_role_id: GlobalRoleId
    assigned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
