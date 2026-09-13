"""Idempotent seed data for identity and access.

Note: the seed is protected by an in-process asyncio lock so concurrent
startups inside the same process do not race. Multi-process bootstrap should
serialize seed execution externally.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.shared.enums import GlobalRoleName, TenantRoleName
from openrag_lab.infrastructure.db.models.identity import (
    GlobalRoleModel,
    PermissionModel,
    RoleModel,
)

_seed_lock = asyncio.Lock()

# (resource, action)
PERMISSION_CATALOG: list[tuple[str, str]] = [
    ("chat", "use"),
    ("search", "use"),
    ("documents", "read"),
    ("documents", "upload"),
    ("documents", "delete"),
    ("users", "read"),
    ("users", "write"),
    ("roles", "read"),
    ("roles", "write"),
    ("tenants", "read"),
    ("tenants", "write"),
]

TENANT_ROLES: dict[TenantRoleName, set[str]] = {
    TenantRoleName.TENANT_ADMIN: {
        "chat:use",
        "search:use",
        "documents:read",
        "documents:upload",
        "documents:delete",
        "users:read",
        "users:write",
        "roles:read",
        "roles:write",
        "tenants:read",
    },
    TenantRoleName.DEVELOPER: {
        "chat:use",
        "search:use",
        "documents:read",
        "documents:upload",
        "documents:delete",
        "users:read",
    },
    TenantRoleName.USER: {
        "chat:use",
        "search:use",
        "documents:read",
        "documents:upload",
    },
    TenantRoleName.VIEWER: {
        "chat:use",
        "search:use",
        "documents:read",
    },
}

GLOBAL_ROLES: dict[GlobalRoleName, set[str]] = {
    GlobalRoleName.SUPER_ADMIN: {
        "chat:use",
        "search:use",
        "documents:read",
        "documents:upload",
        "documents:delete",
        "users:read",
        "users:write",
        "roles:read",
        "roles:write",
        "tenants:read",
        "tenants:write",
    },
}


def _permission_name(resource: str, action: str) -> str:
    return f"{resource}:{action}"


async def seed_identity(session: AsyncSession) -> None:
    """Insert permissions and built-in roles if they do not exist.

    This wrapper serializes seeding with an in-process asyncio lock.
    """
    async with _seed_lock:
        await _seed_identity_locked(session)


async def _seed_identity_locked(session: AsyncSession) -> None:
    """Insert permissions and built-in roles if they do not exist."""

    # Permissions
    existing_permissions = set(
        (await session.scalars(select(PermissionModel.name))).all()
    )
    permission_by_name: dict[str, PermissionModel] = {}
    for resource, action in PERMISSION_CATALOG:
        name = _permission_name(resource, action)
        if name not in existing_permissions:
            model = PermissionModel(
                id=f"perm-{resource}-{action}",
                resource=resource,
                action=action,
                name=name,
            )
            session.add(model)
            permission_by_name[name] = model
        else:
            permission_by_name[name] = (
                await session.scalars(
                    select(PermissionModel).where(PermissionModel.name == name)
                )
            ).one()

    # Tenant roles
    existing_role_names = set((await session.scalars(select(RoleModel.name))).all())
    role_models: dict[TenantRoleName, RoleModel] = {}
    for role_name, permission_names in TENANT_ROLES.items():
        name_value = role_name.value
        if name_value not in existing_role_names:
            role = RoleModel(
                id=f"role-{name_value}",
                name=name_value,
                description=f"Built-in tenant role: {name_value}",
                is_system=True,
            )
            session.add(role)
            role_models[role_name] = role
        else:
            role_models[role_name] = (
                await session.scalars(select(RoleModel).where(RoleModel.name == name_value))
            ).one()
        role_models[role_name].permissions = [
            permission_by_name[p] for p in permission_names if p in permission_by_name
        ]

    # Global roles
    existing_global_names = set((await session.scalars(select(GlobalRoleModel.name))).all())
    for global_role_name, permission_names in GLOBAL_ROLES.items():
        name_value = global_role_name.value
        if name_value not in existing_global_names:
            global_role = GlobalRoleModel(
                id=f"global-role-{name_value}",
                name=name_value,
                description=f"Built-in global role: {name_value}",
                is_system=True,
            )
            session.add(global_role)
        else:
            global_role = (
                await session.scalars(
                    select(GlobalRoleModel).where(GlobalRoleModel.name == name_value)
                )
            ).one()
        global_role.permissions = [
            permission_by_name[p] for p in permission_names if p in permission_by_name
        ]

    await session.flush()
