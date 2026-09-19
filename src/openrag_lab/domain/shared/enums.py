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

    Each cross-service operation gets an intent state (``INDEXING``,
    ``DELETING``) that is committed *before* OpenRAG is called, so a crash at
    any later point leaves a non-terminal row reconciliation can find.
    ``DELETED`` keeps the row as a tombstone: dropping it would destroy the only
    record that this name was ever known here — which is exactly what makes a
    ghost document underivable through the API (design §2, case A).
    """

    INDEXING = "indexing"   # intent written locally, OpenRAG ingest in flight
    INDEXED = "indexed"     # confirmed by OpenRAG; usable
    FAILED = "failed"       # ingest or promotion failed; needs retry/reconcile
    DELETING = "deleting"   # intent written locally, OpenRAG delete in flight
    DELETED = "deleted"     # tombstone: removal concluded, row kept on purpose


class GlobalRoleName(StrEnum):
    SUPER_ADMIN = "super_admin"


class TenantRoleName(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    DEVELOPER = "developer"
    USER = "user"
    VIEWER = "viewer"
