"""FastAPI entrypoint for OpenRAG Lab web application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker

from openrag_lab.api.routers import health
from openrag_lab.application.identity.auth_service import AuthService
from openrag_lab.config import get_settings
from openrag_lab.infrastructure.db.integrity import find_tenants_with_invalid_slug
from openrag_lab.infrastructure.db.seed import seed_identity
from openrag_lab.infrastructure.db.session import create_all, init_db, reset_db
from openrag_lab.interfaces.api.errors import register_exception_handlers
from openrag_lab.interfaces.api.routers import (
    auth,
    chat,
    documents,
    roles,
    search,
    tenants,
    users,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.app_env.lower() == "production":
        insecure_secrets = {
            "dev-secret-change-me-please-override-in-production",
            "change-me-in-production",
        }
        if settings.jwt_secret in insecure_secrets or len(settings.jwt_secret) < 32:
            raise RuntimeError(
                "Refusing to start in production with a default or short JWT secret"
            )
        if settings.bootstrap_admin_password == "admin123" or len(
            settings.bootstrap_admin_password
        ) < 12:
            raise RuntimeError(
                "Refusing to start in production with a default or short bootstrap admin password"
            )

    engine = init_db(settings.database_url)
    try:
        await create_all()
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            await seed_identity(session)
            await session.commit()
            await AuthService(session).ensure_bootstrap_admin()
            invalid_tenants = await find_tenants_with_invalid_slug(session)
        if invalid_tenants:
            # A tenant with a bad slug cannot be served (its slug is its
            # document namespace), so make the data problem visible at startup
            # instead of only as failing requests later.
            logger.error(
                "Tenants with an unusable slug (requests for them will fail): %s",
                ", ".join(f"{tenant_id}={slug!r}" for tenant_id, slug in invalid_tenants),
            )
        yield
    except Exception:
        reset_db()
        raise
    finally:
        await engine.dispose()


app = FastAPI(title="OpenRAG Lab API", version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)

# Identity & Access (s1p2)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tenants.router)
app.include_router(roles.router)

# RAG: tenant-scoped search, chat and documents (s1p3b, s1p3c)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(documents.router)
