"""Tests for tenant document scoping (Phase 1 logical isolation)."""

import pytest

from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Tenant
from openrag_lab.domain.shared.enums import TenantStatus
from openrag_lab.domain.shared.errors import InvalidOperationError
from openrag_lab.domain.shared.ids import TenantId
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
