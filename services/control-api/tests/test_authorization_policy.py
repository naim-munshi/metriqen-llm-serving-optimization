"""Authorization policy contract tests (no HTTP identity integration yet)."""

from uuid import UUID

import pytest

from app.security.policy import (
    ORG_PERMISSIONS,
    PROJECT_PERMISSIONS,
    ROLE_PERMISSIONS,
    Membership,
    Permission,
    Resource,
    Role,
    is_allowed,
)

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
PROJECT_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def member(role: Role, *, org: UUID = ORG_A, projects=frozenset({PROJECT_A})) -> Membership:
    return Membership(organization_id=org, role=role, project_ids=projects)


def test_permission_catalog_covers_every_enum_value_exactly_once() -> None:
    assert ORG_PERMISSIONS.isdisjoint(PROJECT_PERMISSIONS)
    assert ORG_PERMISSIONS | PROJECT_PERMISSIONS == frozenset(Permission)
    assert set(ROLE_PERMISSIONS) == set(Role)
    assert all(perms <= frozenset(Permission) for perms in ROLE_PERMISSIONS.values())


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(Permission))
def test_cross_tenant_is_never_authorized(role: Role, permission: Permission) -> None:
    resource = (
        Resource(ORG_B)
        if permission in ORG_PERMISSIONS
        else Resource(ORG_B, PROJECT_A)
    )
    assert not is_allowed(member(role), permission, resource)


@pytest.mark.parametrize("role", [r for r in Role if r not in (Role.OWNER, Role.ADMIN)])
def test_project_restrictions_for_non_admins(role: Role) -> None:
    for permission in PROJECT_PERMISSIONS:
        assert not is_allowed(member(role), permission, Resource(ORG_A, PROJECT_B))


@pytest.mark.parametrize("role", list(Role))
def test_permissions_are_exactly_the_allowlist(role: Role) -> None:
    for permission in Permission:
        target = Resource(ORG_A) if permission in ORG_PERMISSIONS else Resource(ORG_A, PROJECT_A)
        assert is_allowed(member(role), permission, target) == (
            permission in ROLE_PERMISSIONS[role]
        )


def test_unknown_permission_and_missing_membership_are_denied() -> None:
    scope = Resource(ORG_A, PROJECT_A)
    assert not is_allowed(None, Permission.INFERENCE_INVOKE, scope)
    assert not is_allowed(member(Role.OWNER), "inference.invoke", scope)
    assert not is_allowed(member(Role.OWNER), "anything", scope)


def test_scope_mismatch_fails_closed() -> None:
    admin = member(Role.OWNER)
    assert not is_allowed(admin, Permission.PROJECT_READ, Resource(ORG_A))
    assert not is_allowed(admin, Permission.PROJECT_CREATE, Resource(ORG_A, PROJECT_A))


def test_admin_cannot_transfer_ownership() -> None:
    assert not is_allowed(member(Role.ADMIN), Permission.OWNER_TRANSFER, Resource(ORG_A))
    assert is_allowed(member(Role.OWNER), Permission.OWNER_TRANSFER, Resource(ORG_A))


def test_viewer_cannot_mutate_or_invoke_model() -> None:
    viewer = member(Role.VIEWER)
    resource = Resource(ORG_A, PROJECT_A)
    for action in (
        Permission.MODEL_REGISTER,
        Permission.DEPLOYMENT_MANAGE,
        Permission.BENCHMARK_RUN,
        Permission.INFERENCE_INVOKE,
        Permission.API_KEY_MANAGE,
    ):
        assert not is_allowed(viewer, action, resource)


def test_developer_cannot_manage_api_keys_or_deploy() -> None:
    developer = member(Role.DEVELOPER)
    resource = Resource(ORG_A, PROJECT_A)
    assert is_allowed(developer, Permission.INFERENCE_INVOKE, resource)
    assert not is_allowed(developer, Permission.API_KEY_MANAGE, resource)
    assert not is_allowed(developer, Permission.DEPLOYMENT_MANAGE, resource)


def test_unassigned_engineer_cannot_access_any_project() -> None:
    ml_engineer = member(Role.ML_ENGINEER, projects=frozenset())
    assert not is_allowed(ml_engineer, Permission.BENCHMARK_RUN, Resource(ORG_A, PROJECT_A))


def test_admin_can_access_organization_projects() -> None:
    assert is_allowed(member(Role.ADMIN, projects=frozenset()), Permission.MODEL_READ, Resource(ORG_A, PROJECT_B))
