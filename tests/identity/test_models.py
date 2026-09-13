from openrag_lab.domain.identity.models import (
    GlobalRole,
    Permission,
    Role,
    Tenant,
    User,
)
from openrag_lab.domain.shared.enums import TenantStatus, UserStatus
from openrag_lab.domain.shared.errors import InvalidOperationError
from openrag_lab.domain.shared.ids import GlobalRoleId, RoleId, TenantId, UserId


def test_permission_name() -> None:
    perm = Permission(id="p1", resource="search", action="use")
    assert perm.name == "search:use"


def test_role_has_permission() -> None:
    role = Role(
        id=RoleId("role-user"),
        name="user",
        permissions={"chat:use", "search:use"},
    )
    assert role.has_permission("chat:use")
    assert not role.has_permission("users:write")


def test_global_role_has_permission() -> None:
    role = GlobalRole(
        id=GlobalRoleId("global-super-admin"),
        name="super_admin",
        permissions={"tenants:write"},
    )
    assert role.has_permission("tenants:write")


def test_tenant_disable() -> None:
    tenant = Tenant(id=TenantId("t1"), name="T1", slug="t1")
    tenant.disable()
    assert tenant.status is TenantStatus.DISABLED


def test_tenant_activate_and_is_idempotent() -> None:
    # A fresh object per expectation: asserting two different members on the same
    # attribute narrows the type, and the second assertion then looks impossible.
    tenant = Tenant(id=TenantId("t2"), name="T2", slug="t2")
    tenant.activate()
    assert tenant.status is TenantStatus.ACTIVE
    tenant.activate()  # idempotent
    assert tenant.status is TenantStatus.ACTIVE


def test_user_disable_blocks_operations() -> None:
    user = User(
        id=UserId("u1"),
        tenant_id=TenantId("t1"),
        username="alice",
        password_hash="hash",
    )
    user.disable()
    assert user.status is UserStatus.DISABLED
    try:
        user.ensure_active()
    except InvalidOperationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected InvalidOperationError")
