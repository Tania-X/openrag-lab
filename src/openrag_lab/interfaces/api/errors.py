"""HTTP mapping for domain errors.

Kept in one place so the error contract is identical for the real app and for
tests that assemble their own ``FastAPI`` instance.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openrag_lab.domain.shared.errors import (
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(PermissionDeniedError)
    async def _permission_denied(request, exc: PermissionDeniedError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def _domain_error(request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
