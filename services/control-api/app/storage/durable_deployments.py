"""PostgreSQL-backed tenant deployment *desired state*, not runtime workers.

All writes are serialized under the trusted project row lock; authorization,
generation-CAS, idempotency command insert and desired-state change are atomic.
No API action contacts Ollama or changes running models. Reconciliation will
require a separate authenticated worker interface and a later migration.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.engine import Connection

from app.security.oidc import VerifiedIdentity
from app.security.policy import Membership, Permission, Resource, Role, is_allowed
from app.storage.schema import (
    organization_memberships, project_deployment_commands,
    project_deployments, project_grants, projects,
)


class DeploymentNotFound(Exception):
    """A missing tenant project and an unauthorized project look identical."""


class DeploymentConflict(Exception):
    """Stale generation, reused key with changed operation or name collision."""


MAX_DEPLOYMENTS_PER_PROJECT = 20


class SQLDurableDeploymentStore:
    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("trusted SQLAlchemy Engine required")
        self.engine = engine

    @staticmethod
    def _scope(conn: Connection, principal: VerifiedIdentity, project_id: UUID,
               permission: Permission, *, write: bool) -> UUID:
        stmt = select(projects.c.organization_id).where(projects.c.id == project_id)
        # Every write for a project serializes at one project row, even if the
        # name does not yet exist. PostgreSQL owns cross-replica arbitration.
        if write:
            stmt = stmt.with_for_update()
        org_id = conn.execute(stmt).scalar_one_or_none()
        if org_id is None:
            raise DeploymentNotFound
        membership_stmt = select(organization_memberships.c.role).where(
            organization_memberships.c.issuer == principal.issuer,
            organization_memberships.c.subject == principal.subject,
            organization_memberships.c.organization_id == org_id,
        )
        if write:
            membership_stmt = membership_stmt.with_for_update(read=True)
        membership_row = conn.execute(membership_stmt).one_or_none()
        if membership_row is None:
            raise DeploymentNotFound
        role = Role(membership_row.role)
        grants = frozenset()
        if role not in (Role.OWNER, Role.ADMIN):
            grant_stmt = select(project_grants.c.project_id).where(
                project_grants.c.issuer == principal.issuer,
                project_grants.c.subject == principal.subject,
                project_grants.c.organization_id == org_id,
                project_grants.c.project_id == project_id,
            )
            if write:
                grant_stmt = grant_stmt.with_for_update(read=True)
            grants = frozenset(r.project_id for r in conn.execute(grant_stmt))
        member = Membership(organization_id=org_id, role=role,
                            project_ids=grants)
        if not is_allowed(member, permission,
                          Resource(organization_id=org_id, project_id=project_id)):
            raise DeploymentNotFound
        return org_id

    @staticmethod
    def _serialize(row) -> dict:
        def timestamp(value):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        return {
            "id": str(row.id),
            "organization_id": str(row.organization_id),
            "project_id": str(row.project_id),
            "name": row.name,
            "model_tag": row.model_tag,
            "model_digest": row.model_digest,
            "previous_tag": row.previous_tag,
            "previous_digest": row.previous_digest,
            "desired_state": row.desired_state,
            "desired_generation": row.desired_generation,
            "observed_state": row.observed_state,
            "observed_generation": row.observed_generation,
            "created_at": timestamp(row.created_at),
            "updated_at": timestamp(row.updated_at),
        }

    @staticmethod
    def _fingerprint(action: str, project_id: UUID, name: str, payload: dict) -> str:
        # Canonical hash scopes the operation to its action/project/name,
        # preventing reuse of a key for a different target or mutation.
        encoded = json.dumps(
            {"action": action, "project_id": str(project_id),
             "name": name, "payload": payload},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _existing_command(conn: Connection, principal: VerifiedIdentity,
                          project_id: UUID, key: str, fingerprint: str):
        row = conn.execute(select(
            project_deployment_commands.c.request_hash,
            project_deployment_commands.c.response_json,
        ).where(
            project_deployment_commands.c.project_id == project_id,
            project_deployment_commands.c.actor_issuer == principal.issuer,
            project_deployment_commands.c.actor_subject == principal.subject,
            project_deployment_commands.c.idempotency_key == key,
        )).one_or_none()
        if row is None:
            return None
        if row.request_hash != fingerprint:
            raise DeploymentConflict
        return json.loads(row.response_json)

    @staticmethod
    def _record_command(conn: Connection, principal: VerifiedIdentity,
                        project_id: UUID, deployment_id: UUID, key: str,
                        fingerprint: str, action: str, response: dict,
                        now: datetime) -> None:
        conn.execute(insert(project_deployment_commands).values(
            id=uuid4(), deployment_id=deployment_id,
            project_id=project_id,
            actor_issuer=principal.issuer, actor_subject=principal.subject,
            idempotency_key=key, request_hash=fingerprint, action=action,
            response_json=json.dumps(response, sort_keys=True, separators=(",", ":")),
            created_at=now,
        ))

    def list(self, principal: VerifiedIdentity, project_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            self._scope(conn, principal, project_id, Permission.DEPLOYMENT_READ,
                        write=False)
            rows = conn.execute(select(project_deployments).where(
                project_deployments.c.project_id == project_id,
            ).order_by(project_deployments.c.name).limit(MAX_DEPLOYMENTS_PER_PROJECT)).all()
        return [self._serialize(row) for row in rows]

    def get(self, principal: VerifiedIdentity, project_id: UUID,
            name: str) -> dict:
        with self.engine.connect() as conn:
            self._scope(conn, principal, project_id, Permission.DEPLOYMENT_READ,
                        write=False)
            row = conn.execute(select(project_deployments).where(
                project_deployments.c.project_id == project_id,
                project_deployments.c.name == name,
            )).one_or_none()
            if row is None:
                raise DeploymentNotFound
        return self._serialize(row)

    def create(self, principal: VerifiedIdentity, project_id: UUID,
               name: str, tag: str, digest: str, key: str) -> dict:
        now = datetime.now(timezone.utc)
        fingerprint = self._fingerprint("create", project_id, name,
                                        {"tag": tag, "digest": digest})
        with self.engine.begin() as conn:
            org_id = self._scope(conn, principal, project_id,
                                 Permission.DEPLOYMENT_MANAGE, write=True)
            saved = self._existing_command(conn, principal, project_id,
                                           key, fingerprint)
            if saved is not None:
                return saved
            if conn.execute(select(func.count()).select_from(project_deployments).where(
                project_deployments.c.project_id == project_id,
            )).scalar_one() >= MAX_DEPLOYMENTS_PER_PROJECT:
                raise DeploymentConflict
            # The project-row lock serializes all creators across PostgreSQL
            # replicas. Detect a name collision as a public 409 instead of
            # accidentally exposing the database's UNIQUE violation as 503.
            if conn.execute(select(project_deployments.c.id).where(
                project_deployments.c.project_id == project_id,
                project_deployments.c.name == name,
            )).first() is not None:
                raise DeploymentConflict
            deployment_id = uuid4()
            conn.execute(insert(project_deployments).values(
                id=deployment_id, project_id=project_id, organization_id=org_id,
                name=name, model_tag=tag, model_digest=digest,
                desired_state="running", desired_generation=1,
                observed_state="unreconciled", observed_generation=0,
                created_at=now, updated_at=now,
            ))
            row = conn.execute(select(project_deployments).where(
                project_deployments.c.id == deployment_id,
            )).one()
            response = self._serialize(row)
            self._record_command(conn, principal, project_id, deployment_id,
                                 key, fingerprint, "create", response, now)
            return response

    def change(self, principal: VerifiedIdentity, project_id: UUID, name: str,
               action: str, key: str, expected_generation: int,
               *, tag: str | None = None, digest: str | None = None) -> dict:
        if action not in ("promote", "rollback", "stop", "start"):
            raise ValueError("untrusted deployment action")
        now = datetime.now(timezone.utc)
        fingerprint = self._fingerprint(action, project_id, name, {
            "generation": expected_generation, "tag": tag, "digest": digest,
        })
        with self.engine.begin() as conn:
            self._scope(conn, principal, project_id,
                        Permission.DEPLOYMENT_MANAGE, write=True)
            saved = self._existing_command(conn, principal, project_id,
                                           key, fingerprint)
            if saved is not None:
                return saved
            row = conn.execute(select(project_deployments).where(
                project_deployments.c.project_id == project_id,
                project_deployments.c.name == name,
            ).with_for_update()).one_or_none()
            if row is None:
                raise DeploymentNotFound
            if row.desired_generation != expected_generation:
                raise DeploymentConflict
            changes = {"desired_generation": row.desired_generation + 1,
                       "updated_at": now}
            if action == "promote":
                if tag == row.model_tag and digest == row.model_digest:
                    raise DeploymentConflict
                changes.update(
                    previous_tag=row.model_tag, previous_digest=row.model_digest,
                    model_tag=tag, model_digest=digest,
                )
            elif action == "rollback":
                if row.previous_tag is None or row.previous_digest is None:
                    raise DeploymentConflict
                changes.update(
                    model_tag=row.previous_tag, model_digest=row.previous_digest,
                    previous_tag=row.model_tag, previous_digest=row.model_digest,
                )
            else:
                desired = "running" if action == "start" else "stopped"
                if row.desired_state == desired:
                    raise DeploymentConflict
                changes["desired_state"] = desired
            conn.execute(update(project_deployments).where(
                project_deployments.c.id == row.id,
                project_deployments.c.desired_generation == expected_generation,
            ).values(**changes))
            changed = conn.execute(select(project_deployments).where(
                project_deployments.c.id == row.id,
            )).one()
            response = self._serialize(changed)
            self._record_command(conn, principal, project_id, row.id,
                                 key, fingerprint, action, response, now)
            return response
