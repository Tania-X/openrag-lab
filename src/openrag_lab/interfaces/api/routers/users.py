"""User management API router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from openrag_lab.application.identity.rbac_service import RbacService
from openrag_lab.application.identity.user_service import UserService
from openrag_lab.domain.shared.errors import DomainError
from openrag_lab.interfaces.api.deps import CurrentUser, DbSession, require_permission
from openrag_lab.interfaces.schemas.user import CreateUserRequest, UserResponse

router = APIRouter(tags=["users"])


def _to_response(user) -> UserResponse:
    return UserResponse(
        id=user.id.value,
        tenant_id=user.tenant_id.value,
        username=user.username,
        display_name=user.display_name,
        status=str(user.status.value),
    )


@router.get("/api/users", response_model=list[UserResponse])
async def list_users(
    session: DbSession,
    actor: Annotated[CurrentUser, Depends(require_permission("users:read"))],
) -> list[UserResponse]:
    service = UserService(session)
    users = await service.list_users(actor.tenant_id)
    return [_to_response(u) for u in users]


@router.post("/api/users", response_model=UserResponse)
async def create_user(
    body: CreateUserRequest,
    session: DbSession,
    actor: Annotated[CurrentUser, Depends(require_permission("users:write"))],
) -> UserResponse:
    target_tenant_id = body.tenant_id or actor.tenant_id
    if target_tenant_id != actor.tenant_id:
        rbac = RbacService(session)
        if not await rbac.is_super_admin(actor.user_id):
            raise HTTPException(status_code=403, detail="permission_denied")

    service = UserService(session)
    try:
        user = await service.create_user(
            username=body.username,
            password=body.password,
            tenant_id=target_tenant_id,
            role_name=body.role_name,
            display_name=body.display_name,
        )
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(user)
