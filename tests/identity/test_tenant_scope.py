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


def test_scope_filename_prefixes_and_strips_paths() -> None:
    tenant = _tenant()
    assert tenant.scope_filename("report.pdf") == "acme/report.pdf"
    assert tenant.scope_filename("  报告 2026.pdf ") == "acme/报告 2026.pdf"
    assert tenant.scope_filename("../../etc/passwd") == "acme/passwd"
    assert tenant.scope_filename("nested/dir/report.pdf") == "acme/report.pdf"


@pytest.mark.parametrize("filename", ["", "   ", "/", "..", "a/.."])
def test_scope_filename_rejects_invalid_names(filename: str) -> None:
    with pytest.raises(InvalidOperationError):
        _tenant().scope_filename(filename)


def test_resolve_tenant_scope_uses_shared_key_and_namespace() -> None:
    scope = resolve_tenant_scope(_tenant())
    assert scope.tenant_id == "t1"
    assert scope.document_namespace == "acme/"
    assert scope.api_key == get_settings().openrag_api_key


def test_resolve_tenant_scope_rejects_disabled_tenant() -> None:
    with pytest.raises(InvalidOperationError):
        resolve_tenant_scope(_tenant(status=TenantStatus.DISABLED))
