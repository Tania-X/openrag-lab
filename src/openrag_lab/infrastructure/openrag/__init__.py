"""Adapters that translate openrag-lab tenants into OpenRAG call parameters."""

from openrag_lab.infrastructure.openrag.tenant_scope import (
    TenantScope,
    resolve_tenant_scope,
)

__all__ = ["TenantScope", "resolve_tenant_scope"]
