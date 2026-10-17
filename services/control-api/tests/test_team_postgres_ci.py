"""Isolated PostgreSQL 16 verification of the checked-in team migration."""
import os
from pathlib import Path
from uuid import UUID, uuid4
import psycopg
import pytest
from sqlalchemy import insert, inspect, select
from sqlalchemy.engine import make_url
from app.security.oidc import VerifiedIdentity
from app.security.policy import Role
from app.storage.runtime import load_local_project_store
from app.storage.schema import organizations, organization_memberships, team_audit_events
from app.storage.team import SQLTeamStore, TeamNotFound

def test_team_migration_and_audited_acceptance_on_real_postgres():
    if os.environ.get("METRIQEN_POSTGRES_CI") != "1":
        pytest.skip("isolated PostgreSQL CI not enabled")
    raw = os.environ.get("METRIQEN_TEST_POSTGRES_URL", "")
    url = make_url(raw)
    if not (url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1"
            and url.port == 5432 and url.database == "metriqen_ci"
            and url.username == "metriqen_test" and not url.query):
        pytest.fail("refusing non-CI PostgreSQL database")
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    files = ("0001_tenant_access.sql", "0002_verified_user_profiles.sql",
             "0003_team_invitations_audit.sql",
             "0004_invitation_cancellation.sql")
    with psycopg.connect(host=url.host, port=url.port, dbname=url.database,
                         user=url.username, password=url.password,
                         connect_timeout=3, autocommit=True) as conn:
        for name in files:
            conn.execute((migrations / name).read_text(encoding="utf-8"))
    engine = load_local_project_store({"METRIQEN_DATABASE_URL": raw})._engine
    try:
        assert {"organization_invitations", "team_audit_events"} <= set(inspect(engine).get_table_names())
        org = uuid4()
        issuer = "https://ci-idp.example.test"
        with engine.begin() as conn:
            conn.execute(insert(organizations).values(id=org, name="Team"))
            conn.execute(insert(organization_memberships).values(
                issuer=issuer, subject="owner", organization_id=org, role="owner"))
        owner = VerifiedIdentity(issuer=issuer, subject="owner", audience="control-api")
        alice = VerifiedIdentity(issuer=issuer, subject="alice", audience="control-api")
        store = SQLTeamStore(engine)
        invited = store.invite(owner, org, "alice", Role.VIEWER)
        assert store.accept(alice, UUID(invited["id"]))["role"] == "viewer"
        with pytest.raises(TeamNotFound):
            store.accept(alice, UUID(invited["id"]))
        bob = VerifiedIdentity(issuer=issuer, subject="bob", audience="control-api")
        pending = store.invite(owner, org, "bob", Role.VIEWER)
        assert [item["id"] for item in store.organization_pending(owner, org)] == [pending["id"]]
        store.cancel(owner, org, UUID(pending["id"]))
        assert store.organization_pending(owner, org) == []
        assert store.pending(bob) == []
        with pytest.raises(TeamNotFound):
            store.accept(bob, UUID(pending["id"]))
        with pytest.raises(TeamNotFound):
            store.cancel(owner, org, UUID(pending["id"]))
        with engine.connect() as conn:
            actions = [r.action for r in conn.execute(select(team_audit_events)).all()]
            assert len(actions) == 4
            assert actions.count("invitation.cancelled") == 1
    finally:
        engine.dispose()
