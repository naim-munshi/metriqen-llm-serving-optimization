"""Transactional team invitations, verified acceptance, revocation and audit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, insert, select, update

from app.security.oidc import VerifiedIdentity
from app.security.policy import Permission, Role, ROLE_PERMISSIONS
from app.storage.schema import (
    organizations, organization_memberships, organization_invitations,
    team_audit_events,
)


class TeamNotFound(Exception):
    """No existence or authorization oracle for tenant resources."""


class TeamConflict(Exception):
    """Already invited, already a member or invitation already used."""


class SQLTeamStore:
    INVITE_DAYS = 7
    MAX_PENDING_PER_ORG = 100

    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("trusted SQLAlchemy Engine required")
        self.engine = engine

    @staticmethod
    def _lock_org(conn, org_id: UUID):
        # Serialize acceptance, invitation and revocation per organization.
        if conn.execute(select(organizations.c.id).where(
            organizations.c.id == org_id,
        ).with_for_update()).scalar_one_or_none() is None:
            raise TeamNotFound

    @staticmethod
    def _role(conn, org_id: UUID, issuer: str, subject: str) -> Role | None:
        value = conn.execute(select(organization_memberships.c.role).where(
            organization_memberships.c.organization_id == org_id,
            organization_memberships.c.issuer == issuer,
            organization_memberships.c.subject == subject,
        )).scalar_one_or_none()
        return Role(value) if value is not None else None

    @classmethod
    def _manager(cls, conn, principal: VerifiedIdentity, org_id: UUID) -> Role:
        role = cls._role(conn, org_id, principal.issuer, principal.subject)
        if role not in (Role.OWNER, Role.ADMIN):
            raise TeamNotFound
        return role

    @staticmethod
    def _may_invite(manager: Role, role: Role) -> bool:
        return (manager == Role.OWNER and role != Role.OWNER
                or manager == Role.ADMIN and role not in (Role.OWNER, Role.ADMIN))

    @staticmethod
    def _audit(conn, principal: VerifiedIdentity, org_id: UUID,
               action: str, target: str, role: Role, invite_id: UUID | None = None):
        conn.execute(insert(team_audit_events).values(
            id=uuid4(), organization_id=org_id,
            actor_issuer=principal.issuer, actor_subject=principal.subject,
            action=action, target_issuer=principal.issuer, target_subject=target,
            role=role.value, invitation_id=invite_id,
            created_at=datetime.now(timezone.utc),
        ))

    def invite(self, principal: VerifiedIdentity, org_id: UUID,
               target: str, role: Role) -> dict:
        if target == principal.subject:
            raise TeamConflict
        now = datetime.now(timezone.utc)
        invite_id, expires = uuid4(), now + timedelta(days=self.INVITE_DAYS)
        with self.engine.begin() as conn:
            self._lock_org(conn, org_id)
            manager = self._manager(conn, principal, org_id)
            if not self._may_invite(manager, role):
                raise TeamNotFound
            if self._role(conn, org_id, principal.issuer, target) is not None:
                raise TeamConflict
            pending = conn.execute(select(organization_invitations.c.id).where(
                organization_invitations.c.organization_id == org_id,
                organization_invitations.c.target_issuer == principal.issuer,
                organization_invitations.c.target_subject == target,
                organization_invitations.c.accepted_at.is_(None),
                organization_invitations.c.expires_at > now,
            )).first()
            if pending is not None:
                raise TeamConflict
            count = conn.execute(select(organization_invitations.c.id).where(
                organization_invitations.c.organization_id == org_id,
                organization_invitations.c.accepted_at.is_(None),
                organization_invitations.c.expires_at > now,
            ).limit(self.MAX_PENDING_PER_ORG)).all()
            if len(count) >= self.MAX_PENDING_PER_ORG:
                raise TeamConflict
            conn.execute(insert(organization_invitations).values(
                id=invite_id, organization_id=org_id,
                target_issuer=principal.issuer, target_subject=target,
                role=role.value, inviter_issuer=principal.issuer,
                inviter_subject=principal.subject, created_at=now, expires_at=expires,
            ))
            self._audit(conn, principal, org_id, "invitation.created", target, role, invite_id)
        return {"id": str(invite_id), "organization_id": str(org_id),
                "target_subject": target, "role": role.value,
                "expires_at": expires.isoformat()}

    def pending(self, principal: VerifiedIdentity) -> list[dict]:
        stmt = select(
            organization_invitations.c.id, organization_invitations.c.organization_id,
            organizations.c.name, organization_invitations.c.role,
            organization_invitations.c.expires_at,
        ).join(organizations, organizations.c.id == organization_invitations.c.organization_id).where(
            organization_invitations.c.target_issuer == principal.issuer,
            organization_invitations.c.target_subject == principal.subject,
            organization_invitations.c.accepted_at.is_(None),
            organization_invitations.c.expires_at > datetime.now(timezone.utc),
        ).order_by(organization_invitations.c.created_at,
                   organization_invitations.c.id).limit(100)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [{"id": str(r.id), "organization_id": str(r.organization_id),
                 "organization_name": r.name, "role": r.role,
                 "expires_at": r.expires_at.isoformat()} for r in rows]

    def accept(self, principal: VerifiedIdentity, invite_id: UUID) -> dict:
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            org_id = conn.execute(select(organization_invitations.c.organization_id).where(
                organization_invitations.c.id == invite_id,
                organization_invitations.c.target_issuer == principal.issuer,
                organization_invitations.c.target_subject == principal.subject,
            )).scalar_one_or_none()
            if org_id is None:
                raise TeamNotFound
            self._lock_org(conn, org_id)
            invitation = conn.execute(select(organization_invitations).where(
                organization_invitations.c.id == invite_id,
                organization_invitations.c.target_issuer == principal.issuer,
                organization_invitations.c.target_subject == principal.subject,
            ).with_for_update()).one_or_none()
            if (invitation is None or invitation.accepted_at is not None
                    or invitation.expires_at.replace(tzinfo=timezone.utc) <= now):
                raise TeamNotFound
            inviter_role = self._role(conn, org_id, invitation.inviter_issuer,
                                      invitation.inviter_subject)
            role = Role(invitation.role)
            if inviter_role is None or not self._may_invite(inviter_role, role):
                raise TeamNotFound
            if self._role(conn, org_id, principal.issuer, principal.subject) is not None:
                raise TeamConflict
            conn.execute(insert(organization_memberships).values(
                issuer=principal.issuer, subject=principal.subject,
                organization_id=org_id, role=role.value,
            ))
            conn.execute(update(organization_invitations).where(
                organization_invitations.c.id == invite_id,
            ).values(accepted_at=now))
            self._audit(conn, principal, org_id, "invitation.accepted",
                        principal.subject, role, invite_id)
        return {"organization_id": str(org_id), "role": role.value}

    def members(self, principal: VerifiedIdentity, org_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            self._manager(conn, principal, org_id)
            rows = conn.execute(select(
                organization_memberships.c.issuer,
                organization_memberships.c.subject,
                organization_memberships.c.role,
            ).where(organization_memberships.c.organization_id == org_id).order_by(
                organization_memberships.c.issuer,
                organization_memberships.c.subject,
            ).limit(500)).all()
        return [{"issuer": r.issuer, "subject": r.subject, "role": r.role} for r in rows]

    def revoke(self, principal: VerifiedIdentity, org_id: UUID, target: str):
        with self.engine.begin() as conn:
            self._lock_org(conn, org_id)
            manager = self._manager(conn, principal, org_id)
            role = self._role(conn, org_id, principal.issuer, target)
            if (role is None or role == Role.OWNER
                    or (manager == Role.ADMIN and role == Role.ADMIN)
                    or target == principal.subject):
                raise TeamNotFound
            conn.execute(delete(organization_memberships).where(
                organization_memberships.c.organization_id == org_id,
                organization_memberships.c.issuer == principal.issuer,
                organization_memberships.c.subject == target,
            ))
            self._audit(conn, principal, org_id, "membership.revoked", target, role)

    def audit(self, principal: VerifiedIdentity, org_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            role = self._role(conn, org_id, principal.issuer, principal.subject)
            if role is None or Permission.AUDIT_READ not in ROLE_PERMISSIONS[role]:
                raise TeamNotFound
            rows = conn.execute(select(team_audit_events).where(
                team_audit_events.c.organization_id == org_id,
            ).order_by(team_audit_events.c.created_at.desc(),
                       team_audit_events.c.id.desc()).limit(100)).all()
        return [{"id": str(r.id), "action": r.action,
                 "actor_issuer": r.actor_issuer, "actor_subject": r.actor_subject,
                 "target_issuer": r.target_issuer, "target_subject": r.target_subject,
                 "role": r.role, "invitation_id": str(r.invitation_id) if r.invitation_id else None,
                 "created_at": r.created_at.isoformat()} for r in rows]
