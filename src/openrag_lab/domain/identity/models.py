"""Identity and access domain models.

These are pure Python domain objects. They must not import FastAPI,
SQLAlchemy, or Pydantic. Persistence is handled by infrastructure
repositories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from openrag_lab.domain.shared.enums import DocumentStatus, TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import ConflictError, InvalidOperationError
from openrag_lab.domain.shared.ids import DocumentId, GlobalRoleId, RoleId, TenantId, UserId

#: Longest filename OpenRAG/openrag-lab will store for one document. The tenant
#: namespace is part of it, so the caller-visible name is whatever remains.
MAX_STORED_FILENAME_LENGTH = 512
#: Upper bound for Document.status_reason: it quotes upstream error text, which
#: can be arbitrarily long, and nothing reads more than the first line anyway.
MAX_STATUS_REASON_LENGTH = 200

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
    #: Registry lifecycle. Defaults to INDEXED because a fully-constructed
    #: Document describes a document that *is* usable; the upload path opts into
    #: INDEXING explicitly before it calls OpenRAG.
    status: DocumentStatus = DocumentStatus.INDEXED
    #: Why the row is not in a clean terminal state (kept internal: it can quote
    #: upstream errors). Human-facing context only — machine decisions must read
    #: a real field, never this text.
    status_reason: str | None = None
    #: True when the local state was concluded without a verdict from OpenRAG
    #: (timeout, connection lost). The remote may or may not have applied the
    #: operation, so reconciliation has to verify the row before trusting it —
    #: this flag is the worklist predicate (`WHERE remote_outcome_unknown`), and
    #: it is a column rather than a phrase in ``status_reason`` because parsing
    #: prose to drive recovery breaks the moment the wording changes.
    remote_outcome_unknown: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def touch(self) -> None:
        """Mark the document as modified.

        ``updated_at`` is a creation-time default, so any mutation has to bump
        it explicitly — the repository persists whatever the entity carries.
        """
        self.updated_at = datetime.now(UTC)

    def mark_indexing(self) -> None:
        """Record the intent to index (or re-index) this document.

        Reached from ``INDEXED`` (a replacement upload), ``FAILED`` (a retry)
        and ``DELETED`` (a re-upload of a deleted name — the tombstone is
        resurrected rather than duplicated, which is why re-uploading a deleted
        name needs no new row and no new key). The row itself is kept so
        ``created_at`` and any known OpenRAG id survive.

        ``INDEXING`` and ``DELETING`` are conflicts: two operations must not
        drive the remote state for one name at the same time, and only one of
        the two directions can win. (The stale in-memory copy cannot cause this
        — the delete path refuses to start on an ``INDEXING`` row, so the two
        guards together leave no interleaving that turns a delete into an
        overwrite.)
        """
        if self.status in (DocumentStatus.INDEXING, DocumentStatus.DELETING):
            raise ConflictError(
                f"Document is already being changed ({self.status.value}): "
                f"{self.display_name}"
            )
        self.status = DocumentStatus.INDEXING
        self.status_reason = None
        self.remote_outcome_unknown = False
        self.updated_at = datetime.now(UTC)

    def mark_indexed(self, openrag_document_id: str | None = None) -> None:
        """Promote to ``INDEXED``: OpenRAG confirmed the document.

        Only valid from ``INDEXING`` — promotion without an intent would mean
        the row skipped the state that makes a crash discoverable.
        """
        if self.status is not DocumentStatus.INDEXING:
            raise InvalidOperationError(
                f"Document is not being indexed (status={self.status.value}): "
                f"{self.display_name}"
            )
        # Only overwrite an id we actually received: a replace-duplicates task
        # need not echo one, and dropping the previous id is a silent downgrade.
        if openrag_document_id:
            self.openrag_document_id = openrag_document_id
        self.status = DocumentStatus.INDEXED
        self.status_reason = None
        self.remote_outcome_unknown = False
        self.updated_at = datetime.now(UTC)

    def mark_failed(self, reason: str, *, outcome_unknown: bool = False) -> None:
        """Record that ingestion failed, keeping the row discoverable.

        ``outcome_unknown`` records that OpenRAG never gave a verdict (a
        timeout): the document may exist remotely even though this row says
        ``FAILED``. Marking it is not pessimism — it is the difference between
        "nothing was written" and "we do not know", and only that difference
        tells reconciliation whether a probe is needed.
        """
        if self.status is not DocumentStatus.INDEXING:
            raise InvalidOperationError(
                f"Document is not being indexed (status={self.status.value}): "
                f"{self.display_name}"
            )
        self.status = DocumentStatus.FAILED
        self.status_reason = reason[:MAX_STATUS_REASON_LENGTH]
        self.remote_outcome_unknown = outcome_unknown
        self.updated_at = datetime.now(UTC)

    def mark_deleting(self) -> None:
        """Record the intent to delete: the row survives until OpenRAG agrees.

        This is a hand-rolled finalizer (design §5.2): the registry row is the
        only record that this name was ever registered here, so it must not be
        dropped before the remote side is settled — otherwise a failed delete
        leaves content nobody tracks and nobody can delete through the API.
        """
        if self.status in (
            DocumentStatus.INDEXING,
            DocumentStatus.DELETING,
            DocumentStatus.DELETED,
        ):
            # INDEXING: an upload is in flight; letting the delete through would
            # race with its promotion. DELETING: another delete is already
            # driving the remote. DELETED: there is nothing left to delete (the
            # caller gets 404, not a conflict — see the service).
            raise ConflictError(
                f"Document is already being changed ({self.status.value}): "
                f"{self.display_name}"
            )
        self.status = DocumentStatus.DELETING
        self.status_reason = None
        self.remote_outcome_unknown = False
        self.updated_at = datetime.now(UTC)

    def mark_deleted(self, *, confirmed: bool, detail: str) -> None:
        """Conclude the removal and keep the row as a tombstone.

        ``confirmed`` says whether OpenRAG gave a verdict. A timeout still lands
        here (the tenant-visible effect of the delete already holds: the name is
        out of the retrieval boundary), but it is flagged so reconciliation can
        come back and finish the job instead of trusting a guess.
        """
        if self.status is not DocumentStatus.DELETING:
            raise InvalidOperationError(
                f"Document is not being deleted (status={self.status.value}): "
                f"{self.display_name}"
            )
        self.status = DocumentStatus.DELETED
        self.status_reason = detail[:MAX_STATUS_REASON_LENGTH]
        self.remote_outcome_unknown = not confirmed
        self.updated_at = datetime.now(UTC)

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
