"""Shared enums for identity and tenant domain."""

from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class GlobalRoleName(StrEnum):
    SUPER_ADMIN = "super_admin"


class TenantRoleName(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    DEVELOPER = "developer"
    USER = "user"
    VIEWER = "viewer"
