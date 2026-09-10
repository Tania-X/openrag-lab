"""Tenant application service."""

from __future__ import annotations

import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.identity.models import Tenant
from openrag_lab.domain.shared.enums import TenantStatus
from openrag_lab.domain.shared.errors import AlreadyExistsError
from openrag_lab.domain.shared.ids import TenantId
from openrag_lab.infrastructure.db.repositories.identity import SqlTenantRepository


def slugify(name: str) -> str:
    slug = "-".join(
        part for part in "".join(c if c.isalnum() else "-" for c in name.strip().lower()).split("-") if part
    )
    slug = slug[:128]
    return slug or f"tenant-{secrets.token_hex(4)}"


class TenantService:
    """Create and query tenants."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SqlTenantRepository(session)

    async def create_tenant(self, name: str, slug: str | None = None) -> Tenant:
        slug = slug or slugify(name)
        existing = await self._repo.find_by_slug(slug)
        if existing is not None:
            raise AlreadyExistsError(f"Tenant slug already exists: {slug}")

        tenant = Tenant(id=TenantId.generate(), name=name, slug=slug, status=TenantStatus.ACTIVE)
        await self._repo.save(tenant)
        await self._session.flush()
        return tenant

    async def get_tenant(self, tenant_id: TenantId) -> Tenant | None:
        return await self._repo.find_by_id(tenant_id)

    async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        return await self._repo.find_by_slug(slug)
