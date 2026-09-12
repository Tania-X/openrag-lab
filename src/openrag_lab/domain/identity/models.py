"""Identity and access domain models.

These are pure Python domain objects. They must not import FastAPI,
SQLAlchemy, or Pydantic. Persistence is handled by infrastructure
repositories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import InvalidOperationError
from openrag_lab.domain.shared.ids import DocumentId, GlobalRoleId, RoleId, TenantId, UserId

#: Longest filename OpenRAG/openrag-lab will store for one document. The tenant
#: namespace is part of it, so the caller-visible name is whatever remains.
MAX_STORED_FILENAME_LENGTH = 512

#: Longest user-facing document name.
MAX_DISPLAY_NAME_LENGTH = 512


def scoped_filename(namespace: str, filename: str) -> str:
    """Return ``namespace + filename`` after validating the name.

    The one implementation of the storage-name rule. ``Tenant.scope_filename``
    delegates here, and callers that have a namespace but no ``Tenant`` (the
    legacy-document migration, for example) use it directly, so the rule cannot
    drift between the upload path and tooling.

    Only a plain filename is accepted: path separators, a leading dot and
    surrounding spaces are rejected rather than normalised away, because
    ``a/b.pdf`` and ``b.pdf`` must never collapse into one stored name.

    Callers that receive a client-supplied path (the multipart ``filename`` of
    an upload, which some browsers send as ``C:\\fakepath\\report.pdf``) must
    reduce it to a basename at the boundary before calling this.
    """
    cleaned = filename.strip()
    if (
        not cleaned
        or cleaned != filename
        or cleaned.startswith(".")
        or "/" in cleaned
        or "\\" in cleaned
    ):
        raise InvalidOperationError(f"Invalid document filename: {filename!r}")
    stored = f"{namespace}{cleaned}"
    if len(stored) > MAX_STORED_FILENAME_LENGTH:
        raise InvalidOperationError(
            f"Document filename is too long ({len(stored)} > "
            f"{MAX_STORED_FILENAME_LENGTH}): {filename!r}"
        )
    return stored


@dataclass(slots=True)
class Permission:
    """A single permission such as ``search:use``."""

    id: str
    resource: str
    action: str

    @property
    def name(self) -> str:
        return f"{self.resource}:{self.action}"


@dataclass(slots=True)
class Role:
    """A tenant-level role assigned to users inside a tenant."""

    id: RoleId
    name: str
    description: str = ""
    is_system: bool = False
    permissions: set[str] = field(default_factory=set)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(slots=True)
class GlobalRole:
    """A platform-level role that spans all tenants."""

    id: GlobalRoleId
    name: str
    description: str = ""
    is_system: bool = False
    permissions: set[str] = field(default_factory=set)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


def validate_tenant_slug(slug: str) -> None:
    """Raise when ``slug`` cannot be used as a document namespace.

    The slug is a tenant's document namespace, so it must never contain a path
    separator or whitespace: a slug like ``acme/eu`` would let one tenant's
    namespace swallow another tenant's prefix. It is validated both when a
    Tenant is constructed and when stored rows are checked, so the rule lives
    here rather than inside the dataclass.
    """
    if not slug or slug != slug.strip():
        raise InvalidOperationError(f"Invalid tenant slug: {slug!r}")
    if slug in {".", ".."} or any(
        char.isspace() or char in ("/", "\\") for char in slug
    ):
        raise InvalidOperationError(f"Invalid tenant slug: {slug!r}")


@dataclass(slots=True)
class Tenant:
    """Tenant aggregate root."""

    id: TenantId
    name: str
    slug: str
    status: TenantStatus = TenantStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        validate_tenant_slug(self.slug)

    @property
    def document_namespace(self) -> str:
        """Filename namespace that keeps this tenant's documents apart.

        All tenants share one OpenRAG deployment and one OpenSearch index, so
        tenant isolation is expressed as a filename prefix: documents are
        ingested as ``<namespace><original name>`` and every search adds
        ``filters["data_sources"] = <the tenant's stored filenames>``.

        The slug is the single source of truth for the namespace; it is
        immutable in Phase 1, so the prefix never has to be rewritten.
        """
        return f"{self.slug}/"

    def scope_filename(self, filename: str) -> str:
        """Return the namespaced filename used to store ``filename`` in OpenRAG.

        The rule itself lives in :func:`scoped_filename` so tooling without a
        ``Tenant`` enforces exactly the same constraints.
        """
        return scoped_filename(self.document_namespace, filename)

    def activate(self) -> None:
        if self.status is TenantStatus.ACTIVE:
            return
        self.status = TenantStatus.ACTIVE
        self.updated_at = datetime.now(UTC)

    def disable(self) -> None:
        if self.status is TenantStatus.DISABLED:
            return
        self.status = TenantStatus.DISABLED
        self.updated_at = datetime.now(UTC)

    def ensure_active(self) -> None:
        if self.status is not TenantStatus.ACTIVE:
            raise InvalidOperationError(f"Tenant {self.slug} is not active")


@dataclass(slots=True)
class User:
    """User aggregate root.

    Phase 1: a user belongs to exactly one tenant.
    """

    id: UserId
    tenant_id: TenantId
    username: str
    password_hash: str
    display_name: str | None = None
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def change_password_hash(self, new_password_hash: str) -> None:
        if not new_password_hash:
            raise InvalidOperationError("password hash must not be empty")
        self.password_hash = new_password_hash
        self.updated_at = datetime.now(UTC)

    def activate(self) -> None:
        if self.status is UserStatus.ACTIVE:
            return
        self.status = UserStatus.ACTIVE
        self.updated_at = datetime.now(UTC)

    def disable(self) -> None:
        if self.status is UserStatus.DISABLED:
            return
        self.status = UserStatus.DISABLED
        self.updated_at = datetime.now(UTC)

    def ensure_active(self) -> None:
        if self.status is not UserStatus.ACTIVE:
            raise InvalidOperationError(f"User {self.username} is not active")


@dataclass(frozen=True, slots=True)
class TenantUserRole:
    """Assignment of a role to a user within a tenant.

    Phase 1 allows at most one role per user per tenant, but the
    structure can hold multiple rows if that constraint is relaxed.
    """

    tenant_id: TenantId
    user_id: UserId
    role_id: RoleId
    assigned_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class UserGlobalRole:
    """Assignment of a platform-level role to a user."""

    user_id: UserId
    global_role_id: GlobalRoleId
    assigned_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class Document:
    """A tenant-owned document that lives in the shared OpenRAG index.

    OpenRAG stores one flat filename per chunk and its public API cannot tag
    documents with arbitrary metadata, so openrag-lab keeps this registry:
    it maps the namespaced ``stored_filename`` back to the tenant that owns it
    and provides the filename list used to scope searches.
    """

    id: DocumentId
    tenant_id: TenantId
    stored_filename: str
    display_name: str
    uploaded_by: UserId
    mimetype: str = "application/octet-stream"
    size_bytes: int = 0
    openrag_document_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def rename(self, display_name: str) -> None:
        """Change the user-facing name.

        ``stored_filename`` is immutable: it is the document's identity inside
        OpenRAG and carries the tenant namespace, so renaming it would break
        both tenant isolation and the registry's mapping.
        """
        if not display_name.strip():
            raise InvalidOperationError("Document name must not be empty")
        if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise InvalidOperationError(
                f"Document name is too long ({len(display_name)} > "
                f"{MAX_DISPLAY_NAME_LENGTH})"
            )
        self.display_name = display_name
        self.updated_at = datetime.now(UTC)
