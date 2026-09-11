"""Data integrity checks for locally stored rows.

Import-time and read-path validation (see ``Tenant.__post_init__``) keeps a bad
row from being served, but it cannot report what is wrong with the data set as
a whole. These checks run at startup so a broken row is visible in the logs
instead of only surfacing as a failed request later.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.domain.identity.models import validate_tenant_slug
from openrag_lab.domain.shared.errors import InvalidOperationError
from openrag_lab.infrastructure.db.models.identity import TenantModel


async def find_tenants_with_invalid_slug(
    session: AsyncSession,
) -> list[tuple[str, str]]:
    """Return ``(tenant_id, slug)`` for rows whose slug cannot be a namespace.

    Reads the raw columns instead of building domain objects, so an invalid row
    is reported rather than raised.
    """
    result = await session.execute(select(TenantModel.id, TenantModel.slug))
    invalid: list[tuple[str, str]] = []
    for tenant_id, slug in result.all():
        try:
            validate_tenant_slug(slug)
        except InvalidOperationError:
            invalid.append((tenant_id, slug))
    return invalid
