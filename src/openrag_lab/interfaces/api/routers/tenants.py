"""Tenant API router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from openrag_lab.application.identity.tenant_service import TenantService
from openrag_lab.domain.shared.ids import TenantId
from openrag_lab.interfaces.api.deps import CurrentUser, DbSession, require_permission
from openrag_lab.interfaces.schemas.tenant import TenantResponse

router = APIRouter(tags=["tenants"])


@router.get("/api/tenants/me", response_model=TenantResponse)
async def get_my_tenant(
    session: DbSession,
    actor: Annotated[CurrentUser, Depends(require_permission("tenants:read"))],
) -> TenantResponse:
    service = TenantService(session)
    tenant = await service.get_tenant(TenantId(actor.tenant_id))
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse(
        id=tenant.id.value,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value,
    )
