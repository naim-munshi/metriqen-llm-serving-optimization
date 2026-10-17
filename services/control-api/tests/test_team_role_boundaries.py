"""Owner/admin role ceiling and member removal tests."""
from uuid import UUID
import pytest
from app.security.policy import Role
from app.storage.team import TeamNotFound
from team_test_helpers import seeded, identity

def test_admin_cannot_escalate_or_remove_owner():
    engine, store, org = seeded()
    try:
        admin = store.invite(identity("owner"), org, "admin", Role.ADMIN)
        store.accept(identity("admin"), UUID(admin["id"]))
        for role in (Role.ADMIN, Role.OWNER):
            with pytest.raises(TeamNotFound):
                store.invite(identity("admin"), org, "mallory", role)
        for actor, target in (("admin", "owner"), ("owner", "owner"),
                              ("admin", "admin")):
            with pytest.raises(TeamNotFound):
                store.revoke(identity(actor), org, target)
        with pytest.raises(TeamNotFound):
            store.members(identity("outsider"), org)
        with pytest.raises(TeamNotFound):
            store.audit(identity("outsider"), org)
        viewer = store.invite(identity("admin"), org, "viewer", Role.VIEWER)
        store.accept(identity("viewer"), UUID(viewer["id"]))
        with pytest.raises(TeamNotFound):
            store.members(identity("viewer"), org)
        store.revoke(identity("admin"), org, "viewer")
    finally:
        engine.dispose()
