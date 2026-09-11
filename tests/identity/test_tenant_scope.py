"""Tests for tenant document scoping (Phase 1 logical isolation)."""

import pytest

from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_STORED_FILENAME_LENGTH,
    Document,
    Tenant,
    validate_tenant_slug,
)
from openrag_lab.domain.shared.enums import TenantStatus
from openrag_lab.domain.shared.errors import InvalidOperationError
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId
from openrag_lab.infrastructure.openrag.tenant_scope import resolve_tenant_scope


def _tenant(slug: str = "acme", status: TenantStatus = TenantStatus.ACTIVE) -> Tenant:
    return Tenant(id=TenantId("t1"), name="Acme", slug=slug, status=status)


def test_document_namespace_derives_from_slug() -> None:
    assert _tenant().document_namespace == "acme/"


def test_scope_filename_prefixes_a_plain_filename() -> None:
    tenant = _tenant()
    assert tenant.scope_filename("report.pdf") == "acme/report.pdf"
    assert tenant.scope_filename("报告 2026.pdf") == "acme/报告 2026.pdf"
    assert tenant.scope_filename("report.v2.pdf") == "acme/report.v2.pdf"


@pytest.mark.parametrize(
    "filename",
    [
        "",
        "   ",
        "/",
        "..",
        "a/..",
        "../other/report.pdf",
        "nested/dir/report.pdf",
        "..\\other\\report.pdf",
        " report.pdf",
        "report.pdf ",
        ".hidden",
        ".env",
    ],
)
def test_scope_filename_rejects_anything_but_a_plain_filename(filename: str) -> None:
    with pytest.raises(InvalidOperationError):
        _tenant().scope_filename(filename)


def test_scope_filename_enforces_stored_name_length() -> None:
    tenant = _tenant()
    fits = "a" * (MAX_STORED_FILENAME_LENGTH - len(tenant.document_namespace))
    assert tenant.scope_filename(fits) == f"acme/{fits}"
    with pytest.raises(InvalidOperationError):
        tenant.scope_filename(f"{fits}a")


def test_scope_filename_cannot_collide_with_another_tenants_namespace() -> None:
    acme = _tenant(slug="acme")
    acme2 = _tenant(slug="acme2")
    # Both tenants can only ever produce names inside their own prefix.
    assert acme.scope_filename("budget.md") == "acme/budget.md"
    assert acme2.scope_filename("budget.md") == "acme2/budget.md"
    with pytest.raises(InvalidOperationError):
        acme.scope_filename("acme2/budget.md")


@pytest.mark.parametrize("slug", ["", "  ", ".", "..", "acme/eu", "acme eu", "acme\\eu", "acme\n"])
def test_tenant_slug_must_be_namespace_safe(slug: str) -> None:
    with pytest.raises(InvalidOperationError):
        _tenant(slug=slug)


def test_tenant_slug_allows_unicode_alphanumerics() -> None:
    assert _tenant(slug="星云金融").document_namespace == "星云金融/"


def test_validate_tenant_slug_is_usable_without_building_a_tenant() -> None:
    """Stored rows are checked with this helper, so it must not need an object."""
    validate_tenant_slug("acme")
    with pytest.raises(InvalidOperationError):
        validate_tenant_slug("acme/eu")


def test_resolve_tenant_scope_uses_shared_key_and_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the key instead of reading ambient config: CI has no .env, so a test
    # that depends on a locally configured OPENRAG_API_KEY would be order- and
    # environment-dependent.
    monkeypatch.setattr(get_settings(), "openrag_api_key", "orag_test_key", raising=False)
    scope = resolve_tenant_scope(_tenant())
    assert scope.tenant_id == "t1"
    assert scope.document_namespace == "acme/"
    assert scope.api_key == "orag_test_key"


def test_resolve_tenant_scope_rejects_disabled_tenant() -> None:
    with pytest.raises(InvalidOperationError):
        resolve_tenant_scope(_tenant(status=TenantStatus.DISABLED))


def test_resolve_tenant_scope_requires_a_configured_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "openrag_api_key", "", raising=False)
    with pytest.raises(RuntimeError, match="OPENRAG_API_KEY"):
        resolve_tenant_scope(_tenant())


def _document() -> Document:
    return Document(
        id=DocumentId("d1"),
        tenant_id=TenantId("t1"),
        stored_filename="acme/report.pdf",
        display_name="report.pdf",
        uploaded_by=UserId("u1"),
    )


def test_document_rename_updates_display_name_only() -> None:
    document = _document()
    document.rename("季度报告.pdf")
    assert document.display_name == "季度报告.pdf"
    assert document.stored_filename == "acme/report.pdf"


@pytest.mark.parametrize("name", ["", "   "])
def test_document_rename_rejects_empty_names(name: str) -> None:
    with pytest.raises(InvalidOperationError):
        _document().rename(name)


def test_document_rename_enforces_display_name_length() -> None:
    document = _document()
    document.rename("a" * MAX_DISPLAY_NAME_LENGTH)
    with pytest.raises(InvalidOperationError):
        document.rename("a" * (MAX_DISPLAY_NAME_LENGTH + 1))
