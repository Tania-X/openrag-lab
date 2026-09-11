"""Tenant scoping for OpenRAG calls.

Phase 1 uses **logical isolation** on one shared OpenRAG deployment:

* one shared OpenRAG API key serves every tenant, and
* tenant separation is expressed as a filename namespace, enforced by adding
  ``filters["data_sources"]`` (the tenant's stored filenames) to every search.

A per-tenant OpenRAG API key cannot isolate tenants on its own: OpenRAG derives
a document's ``owner`` from the identity behind the key, and the public API
offers no way to set ``owner`` explicitly, so one key means one owner shared by
every tenant. Real owner-based isolation needs a dedicated OpenRAG user (and
key) per tenant; this module is the single seam to change when that lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Tenant


@dataclass(frozen=True, slots=True)
class TenantScope:
    """Everything needed to talk to OpenRAG on behalf of one tenant."""

    tenant_id: str
    document_namespace: str
    api_key: str


def resolve_tenant_scope(tenant: Tenant) -> TenantScope:
    """Return the OpenRAG scope for ``tenant``."""
    tenant.ensure_active()
    return TenantScope(
        tenant_id=tenant.id.value,
        document_namespace=tenant.document_namespace,
        api_key=get_settings().openrag_api_key,
    )
