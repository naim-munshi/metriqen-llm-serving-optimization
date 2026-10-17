"""Cross-tenant authorization and duplicate invitation denial."""
from uuid import uuid4
import pytest
from sqlalchemy import insert
from app.security.policy import Role
from app.storage.schema import organizations, organization_memberships
from app.storage.team import TeamConflict, TeamNotFound
from team_test_helpers import seeded, identity, ISSUER

def test_cross_tenant_and_pending_limits():
    engine, store, org = seeded()
    other = uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(insert(organizations).values(id=other, name="Other"))
            conn.execute(insert(organization_memberships).values(
                issuer=ISSUER, subject="other-owner", organization_id=other, role="owner"))
        with pytest.raises(TeamNotFound):
            store.invite(identity("other-owner"), org, "alice", Role.ADMIN)
        with pytest.raises(TeamNotFound):
            store.members(identity("owner"), other)
        with pytest.raises(TeamNotFound):
            store.audit(identity("owner"), other)
        with pytest.raises(TeamNotFound):
            store.invite(identity("owner", "https://other-idp"), org, "alice", Role.ADMIN)
        store.MAX_PENDING_PER_ORG = 1
        store.invite(identity("owner"), org, "alice", Role.VIEWER)
        with pytest.raises(TeamConflict):
            store.invite(identity("owner"), org, "alice", Role.VIEWER)
        with pytest.raises(TeamConflict):
            store.invite(identity("owner"), org, "bob", Role.VIEWER)
    finally:
        engine.dispose()
