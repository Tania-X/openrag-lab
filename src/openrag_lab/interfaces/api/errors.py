"""HTTP mapping for domain errors.

Kept in one place so the error contract is identical for the real app and for
tests that assemble their own ``FastAPI`` instance.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openrag_lab.config import ConfigurationError
from openrag_lab.domain.shared.errors import (
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(PermissionDeniedError)
    async def _permission_denied(request, exc: PermissionDeniedError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def _domain_error(request, exc: DomainError) -> JSONResponse:
        # Most subclasses mean "the request cannot be honoured as asked", which
        # is a 400. Some can also come from server-side state (a corrupt tenant
        # row, an over-large scope), so log every one — otherwise such a failure
        # is indistinguishable from a caller mistake.
        logger.warning("Domain error on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ConfigurationError)
    async def _configuration_error(request, exc: ConfigurationError) -> JSONResponse:
        # The operator needs the detail; the caller only needs to know that the
        # service is temporarily unable to serve, not how to probe its config.
        logger.error("Configuration error on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Service is not configured for this operation"},
        )
