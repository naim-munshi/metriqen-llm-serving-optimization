"""Atomic trusted tenant/deployment resolution for guarded local inference.

Never consume role, model, digest, organization, readiness or grant claims from
client JWTs or request headers. The resolved state is a point-in-time
PostgreSQL snapshot, not a continuously valid service capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Engine, select

from app.local_models import DIGEST, MODEL_TAG
from app.security.oidc import VerifiedIdentity
from app.security.policy import Membership, Permission, Resource, Role, is_allowed
from app.storage.schema import (
    organization_memberships, project_deployments, project_grants, projects,
)

ROUTING_FRESHNESS_SECONDS = 120


class RouteNotFound(Exception):
    """No such authorized project/deployment; never leak tenant existence."""


class RouteNotReady(Exception):
    """Existing authorized deployment lacks current, matching ready evidence."""


@dataclass(frozen=True, slots=True)
class DeploymentRoute:
    project_id: UUID
    organization_id: UUID
    deployment: str
    model_tag: str
    model_digest: str
    generation: int


class SQLDeploymentRoutingStore:
    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("trusted PostgreSQL engine required")
        self.engine = engine

    def resolve(
        self, identity: VerifiedIdentity, project_id: UUID, deployment: str,
        *, now: datetime | None = None,
    ) -> DeploymentRoute:
        if not isinstance(identity, VerifiedIdentity) or not isinstance(project_id, UUID):
            raise TypeError("trusted identity and UUID required")
        if not isinstance(deployment, str):
            raise RouteNotFound
        now = now or datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            # Shared project lock serializes eligibility reads with project
            # row-locking lifecycle mutations across *all* API replicas.
            project = conn.execute(select(
                projects.c.id, projects.c.organization_id,
            ).where(projects.c.id == project_id).with_for_update(read=True)).one_or_none()
            if project is None:
                raise RouteNotFound
            member = conn.execute(select(
                organization_memberships.c.role,
            ).where(
                organization_memberships.c.issuer == identity.issuer,
                organization_memberships.c.subject == identity.subject,
                organization_memberships.c.organization_id == project.organization_id,
            ).with_for_update(read=True)).one_or_none()
            if member is None:
                raise RouteNotFound
            role = Role(member.role)
            grants = frozenset()
            if role not in (Role.OWNER, Role.ADMIN):
                grants = frozenset(row.project_id for row in conn.execute(
                    select(project_grants.c.project_id).where(
                        project_grants.c.issuer == identity.issuer,
                        project_grants.c.subject == identity.subject,
                        project_grants.c.organization_id == project.organization_id,
                        project_grants.c.project_id == project_id,
                    ).with_for_update(read=True)
                ))
            membership = Membership(organization_id=project.organization_id,
                                    role=role, project_ids=grants)
            if not is_allowed(
                membership, Permission.INFERENCE_INVOKE,
                Resource(organization_id=project.organization_id,
                         project_id=project_id),
            ):
                raise RouteNotFound
            row = conn.execute(select(project_deployments).where(
                project_deployments.c.organization_id == project.organization_id,
                project_deployments.c.project_id == project_id,
                project_deployments.c.name == deployment,
            ).with_for_update(read=True)).one_or_none()
            if row is None:
                raise RouteNotFound
            observed = row.observed_at
            if observed is not None and observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            if (row.desired_state != "running" or
                row.observed_state != "ready" or
                row.desired_generation != row.observed_generation or
                row.desired_generation < 1 or
                row.observed_code is not None or
                observed is None or
                observed > now or
                observed < now - timedelta(seconds=ROUTING_FRESHNESS_SECONDS) or
                row.reconcile_lease_id is not None or
                MODEL_TAG.fullmatch(row.model_tag) is None or
                DIGEST.fullmatch(row.model_digest) is None):
                raise RouteNotReady
            return DeploymentRoute(
                project_id=project_id, organization_id=project.organization_id,
                deployment=deployment, model_tag=row.model_tag,
                model_digest=row.model_digest,
                generation=row.desired_generation,
            )
