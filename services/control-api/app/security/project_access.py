"""Server-resolved project authorization contract.

No database is shipped with this milestone. Production traffic MUST NOT use a
client-supplied membership, JWT role claim, or test-only in-memory fixture as
an authoritative source of membership.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, Request

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.security.policy import Membership, Permission, Resource, is_allowed


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    """Project scope and presentation data resolved by a trusted backend."""
    project_id: UUID
    organization_id: UUID
    name: str


class ProjectAccessStore(Protocol):
    """Trusted server-side lookup seam for future persistent repositories.

    A real implementation must resolve the project and organization from
    persisted data and obtain the caller's current membership from its own
    authoritative records. Never take membership/roles from JWT or URL.
    """

    def get_project(self, project_id: UUID) -> ProjectRecord | None: ...

    def get_membership(
        self, *, issuer: str, subject: str, organization_id: UUID
    ) -> Membership | None: ...


def read_authorized_project(
    request: Request,
    project_id: str,
    *,
    identity_settings: PinnedIdentitySettings | None,
    store: ProjectAccessStore | None,
) -> dict[str, str]:
    """Authenticate first, then authorize one server-resolved project.

    Unknown, cross-tenant, and forbidden projects are indistinguishable to
    the caller (404), preventing resource-existence probing. Configuration
    and backend errors are generic 503s; no trusted state is synthesized.
    """
    identity = authenticated_identity(request, identity_settings)
    if store is None:
        raise HTTPException(status_code=503, detail="project authorization store is not configured")

    try:
        parsed = UUID(project_id)
        # Use one canonical project key so different textual representations
        # cannot accidentally bypass caches or lookup semantics.
        if str(parsed) != project_id.lower():
            raise ValueError("noncanonical project identifier")
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="project not found") from None

    try:
        project = store.get_project(parsed)
        if not isinstance(project, ProjectRecord) or project.project_id != parsed:
            raise HTTPException(status_code=404, detail="project not found")

        membership = store.get_membership(
            issuer=identity.issuer,
            subject=identity.subject,
            organization_id=project.organization_id,
        )
        if (not isinstance(membership, Membership)
                or not is_allowed(
                    membership, Permission.PROJECT_READ,
                    Resource(organization_id=project.organization_id, project_id=parsed),
                )):
            raise HTTPException(status_code=404, detail="project not found")
        return {
            "id": str(project.project_id),
            "organization_id": str(project.organization_id),
            "name": project.name,
        }
    except HTTPException:
        raise
    except Exception:
        # Backing store transport/parse errors must never accidentally grant
        # access or echo sensitive database details. The route is read-only.
        raise HTTPException(status_code=503, detail="project authorization unavailable") from None
