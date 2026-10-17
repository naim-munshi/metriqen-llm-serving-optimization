"""Cancellation security: authorization ceiling, one-time acceptance, rollback."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from app.security.policy import Role
from app.storage.schema import organization_invitations, team_audit_events
from app.storage.team import SQLTeamStore, TeamNotFound
from team_test_helpers import seeded, identity


def test_cancel_revokes_pending_and_allows_safe_reinvite():
    engine, store, org = seeded()
    try:
        issued = store.invite(identity("owner"), org, "wrong-target", Role.VIEWER)
        invite_id = UUID(issued["id"])
        assert store.organization_pending(identity("owner"), org)[0]["id"] == issued["id"]
        assert len(store.pending(identity("wrong-target"))) == 1
        store.cancel(identity("owner"), org, invite_id)
        assert store.organization_pending(identity("owner"), org) == []
        assert store.pending(identity("wrong-target")) == []
        with pytest.raises(TeamNotFound):
            store.accept(identity("wrong-target"), invite_id)
        with pytest.raises(TeamNotFound):
            store.cancel(identity("owner"), org, invite_id)
        new = store.invite(identity("owner"), org, "wrong-target", Role.VIEWER)
        assert new["id"] != issued["id"]
        with engine.connect() as conn:
            cancelled = conn.execute(select(organization_invitations.c.cancelled_at).where(
                organization_invitations.c.id == invite_id)).scalar_one()
            assert cancelled is not None
            assert len(conn.execute(select(team_audit_events)).all()) == 3
    finally:
        engine.dispose()


def test_admin_cannot_enumerate_or_cancel_owner_issued_admin_grants():
    engine, store, org = seeded()
    try:
        pending_admin = store.invite(identity("owner"), org, "admin", Role.ADMIN)
        store.accept(identity("admin"), UUID(pending_admin["id"]))
        higher = store.invite(identity("owner"), org, "other-admin", Role.ADMIN)
        lower = store.invite(identity("owner"), org, "viewer", Role.VIEWER)
        assert [row["id"] for row in store.organization_pending(identity("admin"), org)] == [lower["id"]]
        with pytest.raises(TeamNotFound):
            store.cancel(identity("admin"), org, UUID(higher["id"]))
        with pytest.raises(TeamNotFound):
            store.organization_pending(identity("outsider"), org)
        with pytest.raises(TeamNotFound):
            store.cancel(identity("outsider"), org, UUID(lower["id"]))
        with pytest.raises(TeamNotFound):
            store.cancel(identity("admin", "https://another-idp"), org, UUID(lower["id"]))
        with pytest.raises(TeamNotFound):
            store.cancel(identity("admin"), uuid4(), UUID(lower["id"]))
        store.cancel(identity("admin"), org, UUID(lower["id"]))
        assert [row["id"] for row in store.organization_pending(identity("owner"), org)] == [higher["id"]]
    finally:
        engine.dispose()


def test_cancel_cannot_change_accepted_invitation_and_audit_failure_rolls_back(monkeypatch):
    engine, store, org = seeded()
    try:
        accepted = store.invite(identity("owner"), org, "accepted", Role.VIEWER)
        store.accept(identity("accepted"), UUID(accepted["id"]))
        with pytest.raises(TeamNotFound):
            store.cancel(identity("owner"), org, UUID(accepted["id"]))
        pending = store.invite(identity("owner"), org, "pending", Role.VIEWER)
        with monkeypatch.context() as patch:
            patch.setattr(SQLTeamStore, "_audit", staticmethod(
                lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rollback probe"))
            ))
            with pytest.raises(RuntimeError):
                store.cancel(identity("owner"), org, UUID(pending["id"]))
        assert store.pending(identity("pending"))[0]["id"] == pending["id"]
        with engine.connect() as conn:
            cancelled = conn.execute(select(organization_invitations.c.cancelled_at).where(
                organization_invitations.c.id == UUID(pending["id"]))).scalar_one()
            assert cancelled is None
        store.cancel(identity("owner"), org, UUID(pending["id"]))
    finally:
        engine.dispose()
