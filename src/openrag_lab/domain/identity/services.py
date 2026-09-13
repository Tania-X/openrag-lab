"""Domain services for identity and access."""

from __future__ import annotations

from openrag_lab.domain.identity.models import GlobalRole, Role, User


def merge_user_permissions(
    user: User,
    tenant_roles: list[Role],
    global_roles: list[GlobalRole],
) -> set[str]:
    """Return the effective permission set for a user.

    Phase 1 combines global-role permissions with tenant-role
    permissions. A super admin is expected to carry a wildcard-like set
    of permissions in the seed data.
    """
    permissions: set[str] = set()
    for tenant_role in tenant_roles:
        permissions.update(tenant_role.permissions)
    for global_role in global_roles:
        permissions.update(global_role.permissions)
    return permissions
