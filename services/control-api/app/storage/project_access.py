"""Read-only, parameter-bound tenant membership adapter for SQLAlchemy.

It implements ProjectAccessStore from the control API's RBAC boundary.
Schema provisioning is a separate privileged migration process; this module
never creates tables, inserts grants or reads database credentials from JWTs.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, select

from app.security.policy import Membership, Role
from app.security.project_access import ProjectRecord
from app.storage.schema import organization_memberships, project_grants, projects


class SQLProjectAccessStore:
    """Query a pre-provisioned SQL database using caller-verified identity.

    This adapter is not automatically connected to the app on startup.
    The caller must provide a trusted, controlled Engine; failures bubble
    up to the HTTP boundary, which returns a generic 503.
    """

    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("trusted SQLAlchemy Engine required")
        self._engine = engine

    def get_project(self, project_id: UUID) -> ProjectRecord | None:
        if not isinstance(project_id, UUID):
            raise TypeError("project identifier must be a UUID")
        statement = select(
            projects.c.id, projects.c.organization_id, projects.c.name,
        ).where(projects.c.id == project_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).one_or_none()
        if row is None:
            return None
        return ProjectRecord(
            project_id=row.id, organization_id=row.organization_id, name=row.name,
        )

    def get_membership(
        self, *, issuer: str, subject: str, organization_id: UUID,
    ) -> Membership | None:
        if (not isinstance(issuer, str) or not issuer
                or not isinstance(subject, str) or not subject
                or not isinstance(organization_id, UUID)):
            raise ValueError("verified subject, issuer and organization are required")
        membership_statement = select(
            organization_memberships.c.role,
        ).where(
            organization_memberships.c.issuer == issuer,
            organization_memberships.c.subject == subject,
            organization_memberships.c.organization_id == organization_id,
        )
        projects_statement = select(
            project_grants.c.project_id,
        ).where(
            project_grants.c.issuer == issuer,
            project_grants.c.subject == subject,
            project_grants.c.organization_id == organization_id,
        )
        # Both reads use the same connection and SQLAlchemy transaction.
        # PostgreSQL's default READ COMMITTED is not a cross-request snapshot;
        # account-level revocation and isolation still need integration tests.
        with self._engine.connect() as connection:
            membership = connection.execute(membership_statement).one_or_none()
            if membership is None:
                return None
            project_ids = frozenset(
                row.project_id for row in connection.execute(projects_statement)
            )

        return Membership(
            organization_id=organization_id,
            role=Role(membership.role),
            project_ids=project_ids,
        )
