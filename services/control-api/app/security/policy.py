"""Deny-by-default organization/project authorization policy.

This policy operates on an already authenticated, server-resolved membership.
It MUST NOT accept membership claims directly from a browser or unverified JWT.
A narrow, read-only HTTP project route now uses this policy with a trusted,
injected membership lookup contract. No production membership persistence,
audit log or other protected business endpoint is implemented.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ML_ENGINEER = "ml_engineer"
    DEPLOYMENT_OPERATOR = "deployment_operator"
    DEVELOPER = "developer"
    AUDITOR = "auditor"
    VIEWER = "viewer"


class Permission(StrEnum):
    ORG_READ = "organization.read"
    MEMBERS_MANAGE = "organization.members.manage"
    OWNER_TRANSFER = "organization.owner.transfer"
    PROJECT_CREATE = "project.create"
    PROJECT_READ = "project.read"
    MODEL_READ = "model.read"
    MODEL_REGISTER = "model.register"
    DEPLOYMENT_READ = "deployment.read"
    DEPLOYMENT_MANAGE = "deployment.manage"
    BENCHMARK_READ = "benchmark.read"
    BENCHMARK_RUN = "benchmark.run"
    INFERENCE_INVOKE = "inference.invoke"
    API_KEY_MANAGE = "api_key.manage"
    USAGE_READ = "usage.read"
    AUDIT_READ = "audit.read"


ORG_PERMISSIONS = frozenset(
    {
        Permission.ORG_READ,
        Permission.MEMBERS_MANAGE,
        Permission.OWNER_TRANSFER,
        Permission.PROJECT_CREATE,
    }
)
PROJECT_PERMISSIONS = frozenset(Permission) - ORG_PERMISSIONS


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(Permission) - {Permission.OWNER_TRANSFER},
    Role.ML_ENGINEER: frozenset(
        {
            Permission.ORG_READ,
            Permission.PROJECT_READ,
            Permission.MODEL_READ,
            Permission.MODEL_REGISTER,
            Permission.DEPLOYMENT_READ,
            Permission.BENCHMARK_READ,
            Permission.BENCHMARK_RUN,
            Permission.INFERENCE_INVOKE,
            Permission.USAGE_READ,
        }
    ),
    Role.DEPLOYMENT_OPERATOR: frozenset(
        {
            Permission.ORG_READ,
            Permission.PROJECT_READ,
            Permission.MODEL_READ,
            Permission.DEPLOYMENT_READ,
            Permission.DEPLOYMENT_MANAGE,
            Permission.BENCHMARK_READ,
            Permission.USAGE_READ,
        }
    ),
    Role.DEVELOPER: frozenset(
        {
            Permission.ORG_READ,
            Permission.PROJECT_READ,
            Permission.MODEL_READ,
            Permission.DEPLOYMENT_READ,
            Permission.BENCHMARK_READ,
            Permission.INFERENCE_INVOKE,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            Permission.ORG_READ,
            Permission.PROJECT_READ,
            Permission.MODEL_READ,
            Permission.DEPLOYMENT_READ,
            Permission.BENCHMARK_READ,
            Permission.USAGE_READ,
            Permission.AUDIT_READ,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.ORG_READ,
            Permission.PROJECT_READ,
            Permission.MODEL_READ,
            Permission.DEPLOYMENT_READ,
            Permission.BENCHMARK_READ,
            Permission.USAGE_READ,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class Membership:
    """Trusted membership loaded from the application database.

    A project-restricted role can access only the listed projects. Owner/admin
    roles are organization-wide by explicit policy, not by implied token scopes.
    """

    organization_id: UUID
    role: Role
    project_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class Resource:
    """Resource scope resolved server-side, not from a client-supplied org ID."""

    organization_id: UUID
    project_id: UUID | None = None


def is_allowed(
    membership: Membership | None,
    permission: Permission | str,
    resource: Resource,
) -> bool:
    """Return True only for a known action within an authorized scope.

    Identity verification is separate from this policy. The new project-read
    HTTP route calls it only after resolving trusted project and membership
    records; other business routes are not yet implemented.
    """
    if membership is None or not isinstance(membership.role, Role):
        return False
    if not isinstance(permission, Permission):
        return False
    if membership.organization_id != resource.organization_id:
        return False

    # The caller must supply the correct organization-versus-project scope.
    if permission in ORG_PERMISSIONS:
        if resource.project_id is not None:
            return False
    elif permission in PROJECT_PERMISSIONS:
        if resource.project_id is None:
            return False
    else:
        return False

    if permission not in ROLE_PERMISSIONS.get(membership.role, frozenset()):
        return False

    if resource.project_id is None:
        return True
    if membership.role in (Role.OWNER, Role.ADMIN):
        return True
    return resource.project_id in membership.project_ids
