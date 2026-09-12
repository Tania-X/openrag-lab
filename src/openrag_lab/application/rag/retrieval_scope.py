"""Tenant scoping for RAG requests.

Every retrieval or chat call must carry an explicit tenant boundary, and that
boundary is decided here rather than by the caller:

* a user is scoped to their own tenant by default;
* a ``tenant_id`` may only point at another tenant for a ``super_admin``;
* the boundary itself is the tenant's registered filenames, which are always
  attached as ``filters["data_sources"]`` — including when the list is empty,
  because OpenRAG treats an empty list as "match nothing" while a *missing*
  key disables filtering entirely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.application.identity.rbac_service import RbacService
from openrag_lab.domain.identity.models import Tenant
from openrag_lab.domain.shared.enums import TenantStatus
from openrag_lab.domain.shared.errors import (
    InvalidOperationError,
    NotFoundError,
    PermissionDeniedError,
)
from openrag_lab.domain.shared.ids import TenantId
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlTenantRepository,
)
from openrag_lab.infrastructure.openrag.tenant_scope import resolve_tenant_scope

logger = logging.getLogger(__name__)

#: Above this many registered documents a request carries a large filename list;
#: worth an operator-visible warning before it turns into a failure.
SCOPED_DOCUMENT_WARN_THRESHOLD = 500

#: Hard ceiling: past this the request is refused here instead of being sent and
#: failing opaquely, because the boundary *is* the filename list and Phase 1 has
#: no bounded alternative (a saved knowledge filter is the fix).
MAX_SCOPED_DOCUMENTS = 5000


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """The tenant boundary a RAG request runs under."""

    tenant_id: str
    document_namespace: str
    api_key: str
    stored_filenames: tuple[str, ...]
    cross_tenant: bool

    @property
    def filters(self) -> dict[str, list[str]]:
        """OpenRAG filters for this scope. The key is always present."""
        return {"data_sources": list(self.stored_filenames)}

    def describe(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "document_count": len(self.stored_filenames),
            "cross_tenant": self.cross_tenant,
        }


class RetrievalScopeResolver:
    """Resolve and authorize the tenant scope for a RAG request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tenant_repo = SqlTenantRepository(session)
        self._document_repo = SqlDocumentRepository(session)
        self._rbac = RbacService(session)

    async def resolve_tenant(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        requested_tenant_id: str | None = None,
    ) -> tuple[Tenant, bool]:
        """Return the authorising tenant and whether it is another tenant.

        Document management and retrieval share this so that "which tenant, and
        may this caller touch it" is decided in exactly one place.
        """
        target_tenant_id = requested_tenant_id or actor_tenant_id
        cross_tenant = target_tenant_id != actor_tenant_id

        # Authorization model for a target tenant other than the caller's own:
        # only a global `super_admin` may read across tenants. Tenant-scoped
        # roles (tenant_admin included) never cross, and there is no
        # "administers tenant X" relationship in Phase 1 — that is why this
        # branch checks the global role directly instead of consulting the
        # tenant-dimension permissions that `require_permission` uses.
        # Narrowing super_admin later means revisiting this line.
        #
        # Order matters: this check runs before the tenant lookup, so a caller
        # who may not cross tenants gets 403 whether or not the target exists.
        # Looking the tenant up first would turn the endpoint into a
        # tenant-enumeration oracle for anyone holding `search:use`
        # (see docs/api-contract.md §2.1).
        if cross_tenant and not await self._rbac.is_super_admin(actor_user_id):
            # Without this check a tenant id would be a read primitive.
            raise PermissionDeniedError("Cross-tenant access requires super_admin")

        tenant = await self._tenant_repo.find_by_id(TenantId(target_tenant_id))
        if tenant is None:
            raise NotFoundError("Tenant not found")
        if tenant.status is not TenantStatus.ACTIVE:
            raise PermissionDeniedError(f"Tenant {tenant.slug} is not active")
        return tenant, cross_tenant

    async def resolve(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        requested_tenant_id: str | None = None,
    ) -> RetrievalScope:
        tenant, cross_tenant = await self.resolve_tenant(
            actor_user_id=actor_user_id,
            actor_tenant_id=actor_tenant_id,
            requested_tenant_id=requested_tenant_id,
        )

        scope = resolve_tenant_scope(tenant)
        filenames = await self._document_repo.list_stored_filenames(tenant.id)
        if len(filenames) > MAX_SCOPED_DOCUMENTS:
            raise InvalidOperationError(
                f"Tenant {tenant.slug} has {len(filenames)} documents, which exceeds "
                f"the scoping limit of {MAX_SCOPED_DOCUMENTS}"
            )
        if len(filenames) > SCOPED_DOCUMENT_WARN_THRESHOLD:
            logger.warning(
                "Large tenant scope: tenant=%s documents=%d (the search body grows "
                "with the filename count)",
                tenant.slug,
                len(filenames),
            )
        return RetrievalScope(
            tenant_id=scope.tenant_id,
            document_namespace=scope.document_namespace,
            api_key=scope.api_key,
            stored_filenames=tuple(filenames),
            cross_tenant=cross_tenant,
        )
