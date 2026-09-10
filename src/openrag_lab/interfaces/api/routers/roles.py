"""Role and permission read API router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openrag_lab.infrastructure.db.repositories.identity import (
    SqlPermissionRepository,
    SqlRoleRepository,
)
from openrag_lab.interfaces.api.deps import CurrentUser, DbSession, require_permission

router = APIRouter(tags=["roles"])


class RoleOut(BaseModel):
    id: str
    name: str
    description: str
    permissions: list[str]


class PermissionOut(BaseModel):
    id: str
    resource: str
    action: str
    name: str


@router.get("/api/roles", response_model=list[RoleOut])
async def list_roles(
    session: DbSession,
    _: Annotated[CurrentUser, Depends(require_permission("roles:read"))],
) -> list[RoleOut]:
    repo = SqlRoleRepository(session)
    roles = await repo.list_all()
    return [
        RoleOut(
            id=r.id.value,
            name=r.name,
            description=r.description,
            permissions=[str(p) for p in sorted(r.permissions)],
        )
        for r in roles
    ]


@router.get("/api/permissions", response_model=list[PermissionOut])
async def list_permissions(
    session: DbSession,
    _: Annotated[CurrentUser, Depends(require_permission("roles:read"))],
) -> list[PermissionOut]:
    repo = SqlPermissionRepository(session)
    permissions = await repo.list_all()
    return [
        PermissionOut(
            id=p.id,
            resource=p.resource,
            action=p.action,
            name=p.name,
        )
        for p in permissions
    ]
