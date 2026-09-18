"""Shared enums for identity and tenant domain."""

from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class DocumentStatus(StrEnum):
    """Lifecycle of a registry row (design: docs/document-registry-state-design.md).

    Only ``INDEXED`` may enter a retrieval scope: the boundary is a filename
    list, and a document that is not confirmed by OpenRAG must not widen it.
    """

    INDEXING = "indexing"   # intent written locally, OpenRAG call in flight
    INDEXED = "indexed"     # confirmed by OpenRAG; usable
    FAILED = "failed"       # ingest or promotion failed; needs retry/reconcile


class GlobalRoleName(StrEnum):
    SUPER_ADMIN = "super_admin"


class TenantRoleName(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    DEVELOPER = "developer"
    USER = "user"
    VIEWER = "viewer"
