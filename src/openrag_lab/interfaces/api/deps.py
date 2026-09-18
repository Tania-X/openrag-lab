"""FastAPI dependencies for authentication and RBAC."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.application.identity.rbac_service import RbacService
from openrag_lab.config import get_settings
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

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

#: RFC 7235: a 401 must carry the challenge for the scheme it wants. FastAPI's
#: HTTPBearer would have sent this itself, but ``auto_error=False`` hands the
#: response to us — so the header comes with the responsibility.
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str) -> HTTPException:
    """Build this module's 401, with the bearer challenge attached.

    The header mapping is copied per call rather than handing the same module-level
    dict to every ``HTTPException``: Starlette only reads it today, but a mutable
    object shared by every 401 in the process is one downstream in-place write away
    from changing all of them.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=dict(_BEARER_CHALLENGE),
    )

DbSession = Annotated[AsyncSession, Depends(get_session)]


#: The gateway owns the per-tenant HTTP client cache, so it has to outlive a
#: request: building one per request would recreate every connection pool. It
#: is therefore a process-level singleton, closed from the app lifespan.
#:
#: Known assumption (raised in review, kept deliberately): **one app instance per
#: process**. Two FastAPI apps sharing this module-level singleton would fight
#: over it — the first app's shutdown closes the clients the second one is using.
#: Test suites are unaffected (they override this dependency), and production runs
#: one app per process. Moving the gateway into ``app.state`` (created and closed
#: in the lifespan) is the shape to adopt if that ever stops being true; it was
#: left out here to stay consistent with ``infrastructure/db/session.py``, which
#: uses the same module-level pattern.
_gateway: RagGateway | None = None


def get_rag_gateway() -> RagGateway:
    """Provide the shared OpenRAG gateway (overridden in tests)."""
    global _gateway
    if _gateway is None:
        _gateway = OpenRAGGateway(
            ingest_timeout=get_settings().upload_ingest_timeout_seconds
        )
    return _gateway


def close_rag_gateway() -> None:
    """Close the shared gateway's cached clients, if one was ever built.

    Called from the lifespan shutdown path. ``None`` is not an error: an
    instance that never served a RAG request has nothing to close.
    """
    global _gateway
    gateway, _gateway = _gateway, None
    if gateway is not None:
        close = getattr(gateway, "close", None)
        if callable(close):
            close()


RagGatewayDep = Annotated[RagGateway, Depends(get_rag_gateway)]


class CurrentUser:
    """The authenticated principal: everything the request needs to act for it.

    Carries ``display_name`` so endpoints like ``/api/auth/me`` can render the
    identity without querying the user row a second time.
    """

    def __init__(
        self,
        user_id: str,
        tenant_id: str,
        username: str,
        display_name: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.username = username
        self.display_name = display_name


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: DbSession,
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized("Authentication required")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        raise _unauthorized("Invalid token")

    user_repo = SqlUserRepository(session)
    user = await user_repo.find_by_id(UserId(user_id))
    if user is None:
        raise _unauthorized("User not found")
    if user.tenant_id.value != tenant_id:
        raise _unauthorized("Invalid token")
    # A disabled *user* answers 401, not 403: the token no longer identifies an
    # active principal, so re-authenticating is the only remedy — the same
    # treatment an expired token gets. A disabled *tenant* (below) is 403: there
    # the principal is valid and only its scope is switched off. Raising 403 here
    # would tell a disabled account "you are authenticated but not allowed",
    # which invites it to keep trying. Both cases are in the contract
    # (docs/api-contract.md §2.1).
    if user.status != UserStatus.ACTIVE:
        raise _unauthorized("User is disabled")

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
        display_name=user.display_name,
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
            # The response stays deliberately generic: an authenticated caller
            # must not be able to enumerate the permission set by reading error
            # messages. The detail belongs in the log instead, or a 403 becomes
            # undebuggable ("which permission was missing, for whom?").
            logger.warning(
                "permission denied: user=%s tenant=%s need=%s",
                current_user.user_id,
                current_user.tenant_id,
                permission,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="permission_denied",
            ) from exc
        return current_user

    return dependency
