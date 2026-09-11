import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.domain.identity.models import Document, Tenant, TenantUserRole, User
from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import AlreadyExistsError, InvalidOperationError
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.integrity import find_tenants_with_invalid_slug
from openrag_lab.infrastructure.db.models.identity import TenantModel
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlRoleRepository,
    SqlTenantRepository,
    SqlTenantUserRoleRepository,
    SqlUserGlobalRoleRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import seed_identity


@pytest.mark.asyncio
async def test_seed_and_repositories() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await seed_identity(session)
        await session.commit()

    async with session_factory() as session:
        tenant_repo = SqlTenantRepository(session)
        tenant = Tenant(
            id=TenantId("t1"),
            name="Acme",
            slug="acme",
        )
        await tenant_repo.save(tenant)

        user_repo = SqlUserRepository(session)
        user = User(
            id=UserId("u1"),
            tenant_id=TenantId("t1"),
            username="alice",
            password_hash="hashed",
        )
        await user_repo.save(user)

        role_repo = SqlRoleRepository(session)
        user_role = await role_repo.find_by_name("user")
        assert user_role is not None

        tenant_user_role_repo = SqlTenantUserRoleRepository(session)
        await tenant_user_role_repo.assign_role(
            TenantUserRole(
                tenant_id=TenantId("t1"),
                user_id=UserId("u1"),
                role_id=user_role.id,
            )
        )

        roles = await tenant_user_role_repo.roles_of_user_in_tenant(
            TenantId("t1"), UserId("u1")
        )
        assert [r.name for r in roles] == ["user"]

        document_repo = SqlDocumentRepository(session)
        await document_repo.save(
            Document(
                id=DocumentId("d1"),
                tenant_id=TenantId("t1"),
                stored_filename="acme/report.pdf",
                display_name="report.pdf",
                uploaded_by=UserId("u1"),
                mimetype="application/pdf",
                size_bytes=1234,
                openrag_document_id="orag-doc-1",
            )
        )

        global_role_repo = SqlUserGlobalRoleRepository(session)
        # no global roles assigned by default
        assert await global_role_repo.global_roles_of_user(UserId("u1")) == []

        await session.commit()

    async with session_factory() as session:
        tenant_repo = SqlTenantRepository(session)
        saved = await tenant_repo.find_by_slug("acme")
        assert saved is not None
        assert saved.name == "Acme"
        assert saved.status is TenantStatus.ACTIVE
        assert saved.document_namespace == "acme/"

        user_repo = SqlUserRepository(session)
        saved_user = await user_repo.find_by_username("alice")
        assert saved_user is not None
        assert saved_user.status is UserStatus.ACTIVE

        document_repo = SqlDocumentRepository(session)
        document = await document_repo.find_by_stored_filename(
            TenantId("t1"), "acme/report.pdf"
        )
        assert document is not None
        assert document.display_name == "report.pdf"
        assert document.openrag_document_id == "orag-doc-1"
        assert await document_repo.list_stored_filenames(TenantId("t1")) == [
            "acme/report.pdf"
        ]

        await document_repo.delete(DocumentId("d1"))
        await session.commit()

    async with session_factory() as session:
        document_repo = SqlDocumentRepository(session)
        assert await document_repo.list_by_tenant(TenantId("t1")) == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_document_registry_keeps_tenants_apart() -> None:
    """The registry is what scopes search, so it must never leak across tenants."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        tenant_repo = SqlTenantRepository(session)
        user_repo = SqlUserRepository(session)
        document_repo = SqlDocumentRepository(session)

        for tenant_id, slug, username in (
            ("t1", "acme", "alice"),
            ("t2", "globex", "bob"),
        ):
            await tenant_repo.save(
                Tenant(id=TenantId(tenant_id), name=slug.title(), slug=slug)
            )
            await user_repo.save(
                User(
                    id=UserId(f"u-{tenant_id}"),
                    tenant_id=TenantId(tenant_id),
                    username=username,
                    password_hash="hashed",
                )
            )
        await session.flush()

        acme = await tenant_repo.find_by_id(TenantId("t1"))
        globex = await tenant_repo.find_by_id(TenantId("t2"))
        assert acme is not None and globex is not None

        await document_repo.save(
            Document(
                id=DocumentId("d-acme"),
                tenant_id=acme.id,
                stored_filename=acme.scope_filename("report.pdf"),
                display_name="report.pdf",
                uploaded_by=UserId("u-t1"),
            )
        )
        await document_repo.save(
            Document(
                id=DocumentId("d-globex"),
                tenant_id=globex.id,
                stored_filename=globex.scope_filename("report.pdf"),
                display_name="report.pdf",
                uploaded_by=UserId("u-t2"),
            )
        )
        await session.commit()

    async with session_factory() as session:
        document_repo = SqlDocumentRepository(session)
        assert await document_repo.list_stored_filenames(TenantId("t1")) == [
            "acme/report.pdf"
        ]
        assert await document_repo.list_stored_filenames(TenantId("t2")) == [
            "globex/report.pdf"
        ]
        assert await document_repo.find_by_stored_filename(
            TenantId("t1"), "globex/report.pdf"
        ) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_saving_a_taken_filename_raises_a_domain_error() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await SqlTenantRepository(session).save(
            Tenant(id=TenantId("t1"), name="Acme", slug="acme")
        )
        await SqlUserRepository(session).save(
            User(
                id=UserId("u1"),
                tenant_id=TenantId("t1"),
                username="alice",
                password_hash="hashed",
            )
        )
        await session.flush()

        document_repo = SqlDocumentRepository(session)
        first = Document(
            id=DocumentId("d1"),
            tenant_id=TenantId("t1"),
            stored_filename="acme/report.pdf",
            display_name="report.pdf",
            uploaded_by=UserId("u1"),
        )
        await document_repo.save(first)

        # Same stored filename, different record: the registry key is taken.
        duplicate = Document(
            id=DocumentId("d2"),
            tenant_id=TenantId("t1"),
            stored_filename="acme/report.pdf",
            display_name="report.pdf",
            uploaded_by=UserId("u1"),
        )
        with pytest.raises(AlreadyExistsError):
            await document_repo.save(duplicate)

        # Re-saving the same record stays an update, not a clash.
        first.rename("report-2026.pdf")
        await document_repo.save(first)
        await session.commit()

    async with session_factory() as session:
        document = await SqlDocumentRepository(session).find_by_id(DocumentId("d1"))
        assert document is not None
        assert document.display_name == "report-2026.pdf"

    await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_stored_slug_is_reported_and_not_served() -> None:
    """Startup check reports the row; reading it fails with a diagnosable error."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        # Write the bad row through the ORM: the domain invariant only guards
        # the code paths that go through a Tenant object.
        session.add(
            TenantModel(
                id="bad-tenant",
                name="Bad",
                slug="acme/eu",
                status=TenantStatus.ACTIVE.value,
            )
        )
        await session.commit()

    async with session_factory() as session:
        assert await find_tenants_with_invalid_slug(session) == [("bad-tenant", "acme/eu")]
        with pytest.raises(InvalidOperationError, match="bad-tenant"):
            await SqlTenantRepository(session).find_by_id(TenantId("bad-tenant"))

    await engine.dispose()
