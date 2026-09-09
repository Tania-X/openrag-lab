"""User management API router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

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
        status=user.status.value,
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
    _: Annotated[CurrentUser, Depends(require_permission("users:write"))],
) -> UserResponse:
    service = UserService(session)
    try:
        user = await service.create_user(
            username=body.username,
            password=body.password,
            tenant_id=body.tenant_id,
            role_name=body.role_name,
            display_name=body.display_name,
        )
        await session.commit()
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(user)
