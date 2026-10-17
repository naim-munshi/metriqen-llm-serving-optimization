"""Audit write failure must roll back membership and invitation writes."""
from uuid import UUID
import pytest
from sqlalchemy import select
from app.security.policy import Role
from app.storage.schema import organization_invitations, organization_memberships, team_audit_events
from app.storage.team import SQLTeamStore
from team_test_helpers import seeded, identity

def test_audit_failure_rolls_back_invitation_and_acceptance(monkeypatch):
    engine, store, org = seeded()
    original = SQLTeamStore._audit
    def failure(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    try:
        monkeypatch.setattr(SQLTeamStore, "_audit", staticmethod(failure))
        with pytest.raises(RuntimeError):
            store.invite(identity("owner"), org, "alice", Role.VIEWER)
        with engine.connect() as conn:
            assert conn.execute(select(organization_invitations)).all() == []
            assert conn.execute(select(team_audit_events)).all() == []
        monkeypatch.setattr(SQLTeamStore, "_audit", staticmethod(original))
        invite = store.invite(identity("owner"), org, "alice", Role.VIEWER)
        monkeypatch.setattr(SQLTeamStore, "_audit", staticmethod(failure))
        with pytest.raises(RuntimeError):
            store.accept(identity("alice"), UUID(invite["id"]))
        with engine.connect() as conn:
            assert conn.execute(select(organization_memberships).where(
                organization_memberships.c.subject == "alice")).all() == []
            assert conn.execute(select(organization_invitations.c.accepted_at)).one().accepted_at is None
    finally:
        engine.dispose()
