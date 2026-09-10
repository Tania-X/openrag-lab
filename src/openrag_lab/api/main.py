"""FastAPI entrypoint for OpenRAG Lab web application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker

from openrag_lab.api.routers import chat, documents, health, search
from openrag_lab.application.identity.auth_service import AuthService
from openrag_lab.config import get_settings
from openrag_lab.infrastructure.db.seed import seed_identity
from openrag_lab.infrastructure.db.session import create_all, init_db
from openrag_lab.interfaces.api.routers import auth, roles, tenants, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.app_env.lower() == "production":
        if settings.jwt_secret.startswith("dev-secret") or settings.bootstrap_admin_password == "admin123":
            raise RuntimeError(
                "Refusing to start in production with default JWT secret or bootstrap admin password"
            )

    engine = init_db()
    try:
        await create_all()
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            await seed_identity(session)
            await session.commit()
            await AuthService(session).ensure_bootstrap_admin()
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="OpenRAG Lab API", version="0.1.0", lifespan=lifespan)

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
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(documents.router)

# Identity & Access (s1p2)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tenants.router)
app.include_router(roles.router)
