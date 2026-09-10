"""Auth API router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from openrag_lab.application.identity.auth_service import AuthService
from openrag_lab.config import get_settings
from openrag_lab.domain.shared.errors import AlreadyExistsError, DomainError, InvalidOperationError
from openrag_lab.interfaces.api.deps import CurrentUser, DbSession, get_current_user
from openrag_lab.interfaces.api.rate_limit import rate_limit
from openrag_lab.interfaces.schemas.auth import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(tags=["auth"])


@router.post("/api/auth/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    session: DbSession,
    _: None = Depends(rate_limit(limit=10, window_seconds=60)),
) -> TokenResponse:
    if not get_settings().allow_self_registration:
        raise HTTPException(status_code=403, detail="self_registration_disabled")
    service = AuthService(session)
    try:
        result = await service.register(
            body.username,
            body.password,
            display_name=body.display_name,
            tenant_name=body.tenant_name,
        )
    except AlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TokenResponse(**result)


@router.post("/api/auth/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: DbSession,
    _: None = Depends(rate_limit(limit=10, window_seconds=60)),
) -> TokenResponse:
    service = AuthService(session)
    try:
        result = await service.login(body.username, body.password)
    except InvalidOperationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return TokenResponse(**result)


@router.get("/api/auth/me", response_model=MeResponse)
async def me(
    session: DbSession,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> MeResponse:
    service = AuthService(session)
    result = await service.me(current_user.user_id, current_user.tenant_id)
    return MeResponse(**result)
