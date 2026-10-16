"""Exact OIDC subject acceptance and replay denial."""
from uuid import UUID
import pytest
from sqlalchemy import select
from app.security.policy import Role
from app.storage.schema import team_audit_events
from app.storage.team import TeamConflict, TeamNotFound
from team_test_helpers import seeded, identity

def test_targeted_single_use_acceptance_and_durable_events():
    engine, store, org = seeded()
    try:
        invitation = store.invite(identity("owner"), org, "alice", Role.VIEWER)
        assert len(store.pending(identity("alice"))) == 1
        assert store.pending(identity("alice", "https://different.example")) == []
        with pytest.raises(TeamNotFound):
            store.accept(identity("mallory"), UUID(invitation["id"]))
        assert store.accept(identity("alice"), UUID(invitation["id"]))["role"] == "viewer"
        with pytest.raises(TeamNotFound):
            store.accept(identity("alice"), UUID(invitation["id"]))
        with pytest.raises(TeamConflict):
            store.invite(identity("owner"), org, "alice", Role.ADMIN)
        store.revoke(identity("owner"), org, "alice")
        assert [m["subject"] for m in store.members(identity("owner"), org)] == ["owner"]
        with engine.connect() as conn:
            assert len(conn.execute(select(team_audit_events)).all()) == 3
    finally:
        engine.dispose()
