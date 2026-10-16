"""Transactional, authenticated organization and project persistence.

PostgreSQL must be provisioned using explicit migrations; no DDL/roles are
created automatically. All identity and permission decisions are server-side.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.engine import Connection

from app.security.oidc import VerifiedIdentity
from app.security.policy import Permission, Role, Resource, Membership, is_allowed
from app.storage.schema import organizations, projects, organization_memberships, project_grants


class WorkspaceNotFound(Exception):
    """Unknown or unauthorized tenant resources return the same HTTP status."""


class WorkspaceLimitReached(Exception):
    pass


class SQLWorkspaceStore:
    MAX_ORGANIZATIONS = 20
    MAX_PROJECTS_PER_ORGANIZATION = 100

    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("trusted SQLAlchemy Engine required")
        self.engine = engine

    @staticmethod
    def _membership(conn: Connection, identity: VerifiedIdentity, organization_id: UUID,
                    *, lock: bool = False) -> Membership | None:
        stmt = select(organization_memberships.c.role).where(
            organization_memberships.c.issuer == identity.issuer,
            organization_memberships.c.subject == identity.subject,
            organization_memberships.c.organization_id == organization_id,
        )
        if lock:
            stmt = stmt.with_for_update(read=True)
        row = conn.execute(stmt).one_or_none()
        if row is None:
            return None
        return Membership(organization_id=organization_id, role=Role(row.role))

    def create_organization(self, identity: VerifiedIdentity, name: str) -> dict:
        new_id = uuid4()
        # A single transaction guarantees no orphan org or ownerless tenant.
        with self.engine.begin() as conn:
            count = conn.execute(select(func.count()).select_from(organization_memberships).where(
                organization_memberships.c.issuer == identity.issuer,
                organization_memberships.c.subject == identity.subject,
                organization_memberships.c.role == Role.OWNER.value,
            )).scalar_one()
            if count >= self.MAX_ORGANIZATIONS:
                raise WorkspaceLimitReached
            conn.execute(insert(organizations).values(id=new_id, name=name))
            conn.execute(insert(organization_memberships).values(
                issuer=identity.issuer, subject=identity.subject,
                organization_id=new_id, role=Role.OWNER.value,
            ))
        return {"id": str(new_id), "name": name, "role": Role.OWNER.value}

    def list_organizations(self, identity: VerifiedIdentity) -> list[dict]:
        stmt = select(
            organizations.c.id, organizations.c.name, organization_memberships.c.role
        ).join(
            organization_memberships,
            organizations.c.id == organization_memberships.c.organization_id,
        ).where(
            organization_memberships.c.issuer == identity.issuer,
            organization_memberships.c.subject == identity.subject,
        ).order_by(organizations.c.name, organizations.c.id).limit(500)
        with self.engine.connect() as conn:
            records = conn.execute(stmt).all()
        return [{"id": str(r.id), "name": r.name, "role": r.role} for r in records]

    def create_project(self, identity: VerifiedIdentity,
                       organization_id: UUID, name: str) -> dict:
        project_id = uuid4()
        with self.engine.begin() as conn:
            membership = self._membership(conn, identity, organization_id, lock=True)
            if not is_allowed(membership, Permission.PROJECT_CREATE,
                              Resource(organization_id=organization_id)):
                raise WorkspaceNotFound
            count = conn.execute(select(func.count()).select_from(projects).where(
                projects.c.organization_id == organization_id,
            )).scalar_one()
            if count >= self.MAX_PROJECTS_PER_ORGANIZATION:
                raise WorkspaceLimitReached
            conn.execute(insert(projects).values(
                id=project_id, organization_id=organization_id, name=name,
            ))
        return {"id": str(project_id), "organization_id": str(organization_id),
                "name": name}

    def list_projects(self, identity: VerifiedIdentity,
                      organization_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            membership = self._membership(conn, identity, organization_id)
            if not is_allowed(membership, Permission.ORG_READ,
                              Resource(organization_id=organization_id)):
                raise WorkspaceNotFound
            stmt = select(
                projects.c.id, projects.c.name
            ).where(projects.c.organization_id == organization_id)
            if membership.role not in (Role.OWNER, Role.ADMIN):
                stmt = stmt.join(project_grants, (
                    (project_grants.c.project_id == projects.c.id) &
                    (project_grants.c.organization_id == organization_id) &
                    (project_grants.c.issuer == identity.issuer) &
                    (project_grants.c.subject == identity.subject)
                ))
            records = conn.execute(stmt.order_by(projects.c.name, projects.c.id).limit(100)).all()
        return [{"id": str(row.id), "organization_id": str(organization_id),
                 "name": row.name} for row in records]
