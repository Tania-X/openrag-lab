"""SQLAlchemy ORM models for identity and access.

These models live in infrastructure only. Domain services never import
them directly; repositories convert between ORM rows and domain objects.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.infrastructure.db.base import Base

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", String(64), ForeignKey("roles.id"), primary_key=True),
    Column(
        "permission_id", String(64), ForeignKey("permissions.id"), primary_key=True
    ),
)

global_role_permissions = Table(
    "global_role_permissions",
    Base.metadata,
    Column(
        "global_role_id", String(64), ForeignKey("global_roles.id"), primary_key=True
    ),
    Column(
        "permission_id", String(64), ForeignKey("permissions.id"), primary_key=True
    ),
)

tenant_user_roles = Table(
    "tenant_user_roles",
    Base.metadata,
    Column("tenant_id", String(64), ForeignKey("tenants.id"), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id"), primary_key=True),
    Column("role_id", String(64), ForeignKey("roles.id"), primary_key=True),
    Column(
        "assigned_at", DateTime(timezone=True), default=lambda: datetime.now(UTC)
    ),
)

user_global_roles = Table(
    "user_global_roles",
    Base.metadata,
    Column("user_id", String(64), ForeignKey("users.id"), primary_key=True),
    Column(
        "global_role_id", String(64), ForeignKey("global_roles.id"), primary_key=True
    ),
    Column(
        "assigned_at", DateTime(timezone=True), default=lambda: datetime.now(UTC)
    ),
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TenantModel(Base):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("slug", name="uq_tenants_slug"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[TenantStatus] = mapped_column(
        SAEnum(TenantStatus, native_enum=False, validate_strings=True, length=32),
        default=TenantStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), index=True
    )
    username: Mapped[str] = mapped_column(String(255), index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, native_enum=False, validate_strings=True, length=32),
        default=UserStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RoleModel(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", name="uq_roles_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    permissions: Mapped[list[PermissionModel]] = relationship(
        secondary=role_permissions, lazy="selectin"
    )


class GlobalRoleModel(Base):
    __tablename__ = "global_roles"
    __table_args__ = (UniqueConstraint("name", name="uq_global_roles_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    permissions: Mapped[list[PermissionModel]] = relationship(
        secondary=global_role_permissions, lazy="selectin"
    )


class PermissionModel(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),
        UniqueConstraint("name", name="uq_permissions_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255), index=True)


class DocumentModel(Base):
    """Registry of tenant-owned documents stored in the shared OpenRAG index."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "stored_filename", name="uq_documents_tenant_filename"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), index=True
    )
    stored_filename: Mapped[str] = mapped_column(String(512), index=True)
    display_name: Mapped[str] = mapped_column(String(512))
    uploaded_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"))
    mimetype: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    openrag_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
