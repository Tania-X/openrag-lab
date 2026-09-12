"""FastAPI dependencies for authentication and RBAC."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.application.identity.rbac_service import RbacService
from openrag_lab.domain.rag.ports import RagGateway
from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import PermissionDeniedError
from openrag_lab.domain.shared.ids import UserId
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlTenantRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.session import get_session
from openrag_lab.infrastructure.openrag.openrag_port_impl import OpenRAGGateway
from openrag_lab.infrastructure.security.jwt import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_rag_gateway() -> RagGateway:
    """Provide the OpenRAG gateway (overridden in tests)."""
    return OpenRAGGateway()


RagGatewayDep = Annotated[RagGateway, Depends(get_rag_gateway)]


class CurrentUser:
    def __init__(self, user_id: str, tenant_id: str, username: str) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.username = username


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: DbSession,
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_repo = SqlUserRepository(session)
    user = await user_repo.find_by_id(UserId(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.tenant_id.value != tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is disabled")

    # A disabled tenant means nobody acts on its behalf — including a global
    # super_admin who happens to belong to it (otherwise that account could
    # keep reading other tenants' documents after its tenant was switched off).
    # This is the single place that establishes "who is acting", so every
    # protected endpoint inherits the rule.
    tenant = await SqlTenantRepository(session).find_by_id(user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant not found")
    if tenant.status is not TenantStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is disabled"
        )

    return CurrentUser(
        user_id=user.id.value,
        tenant_id=user.tenant_id.value,
        username=user.username,
    )


def require_permission(permission: str) -> Callable:
    async def dependency(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        session: DbSession,
    ) -> CurrentUser:
        rbac = RbacService(session)
        try:
            await rbac.assert_permission(current_user.user_id, current_user.tenant_id, permission)
        except PermissionDeniedError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="permission_denied",
            ) from exc
        return current_user

    return dependency
