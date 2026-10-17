"""Expired invitations and revoked inviters cannot create members."""
from datetime import datetime, timedelta, timezone
from uuid import UUID
import pytest
from sqlalchemy import select, update
from app.security.policy import Role
from app.storage.schema import organization_invitations, organization_memberships
from app.storage.team import TeamNotFound
from team_test_helpers import seeded, identity

def test_expired_invite_and_removed_inviter_fail_closed():
    engine, store, org = seeded()
    try:
        expired = store.invite(identity("owner"), org, "expired", Role.VIEWER)
        with engine.begin() as conn:
            conn.execute(update(organization_invitations).where(
                organization_invitations.c.id == UUID(expired["id"])
            ).values(created_at=datetime.now(timezone.utc)-timedelta(days=2),
                     expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        with pytest.raises(TeamNotFound):
            store.accept(identity("expired"), UUID(expired["id"]))
        admin = store.invite(identity("owner"), org, "admin", Role.ADMIN)
        store.accept(identity("admin"), UUID(admin["id"]))
        invite = store.invite(identity("admin"), org, "late", Role.DEVELOPER)
        store.revoke(identity("owner"), org, "admin")
        with pytest.raises(TeamNotFound):
            store.accept(identity("late"), UUID(invite["id"]))
        with engine.connect() as conn:
            assert conn.execute(select(organization_memberships).where(
                organization_memberships.c.subject == "late")).all() == []
    finally:
        engine.dispose()
