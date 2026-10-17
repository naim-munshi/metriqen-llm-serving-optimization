"""Adversarial HTTP tests for the verified bearer team-management surface.

Synthetic access tokens only; SQLite exercises policy while isolated
PostgreSQL CI separately exercises the real migration and transaction model.
"""
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.storage.team import SQLTeamStore
from app.storage.schema import organization_memberships, team_audit_events
from test_workspaces import setup as workspace_setup, auth


@pytest.fixture
def team_setup(workspace_setup):
    _, private_key, engine, workspaces, settings = workspace_setup
    client = TestClient(create_app(
        settings, workspace_store=workspaces, team_store=SQLTeamStore(engine),
    ))
    owner = auth(private_key, user="owner")
    org = client.post("/api/v1/organizations", json={"name": "Team"}, headers=owner)
    assert org.status_code == 201
    return client, private_key, engine, org.json()["id"]


def url(org, suffix):
    return f"/api/v1/organizations/{org}/{suffix}"


def invite(client, key, org, target, role="viewer", actor="owner", **claims):
    return client.post(url(org, "invitations"),
                       json={"target_subject": target, "role": role},
                       headers=auth(key, user=actor, **claims))


def test_verified_target_acceptance_and_immediate_revocation(team_setup):
    client, key, engine, org = team_setup
    alice = auth(key, user="alice")
    invited = invite(client, key, org, "alice")
    assert invited.status_code == 201
    invite_id = invited.json()["id"]
    assert client.get("/api/v1/invitations", headers=alice).json()["invitations"][0]["id"] == invite_id
    assert client.post(f"/api/v1/invitations/{invite_id}/accept",
                       headers=auth(key, user="mallory")).status_code == 404
    assert client.post(f"/api/v1/invitations/{invite_id}/accept", headers=alice).json() == {
        "organization_id": org, "role": "viewer",
    }
    assert client.post(f"/api/v1/invitations/{invite_id}/accept", headers=alice).status_code == 404
    members = client.get(url(org, "members"), headers=auth(key, user="owner"))
    assert {m["subject"] for m in members.json()["members"]} == {"owner", "alice"}
    revoke = client.post(url(org, "members/revoke"), json={"target_subject": "alice"},
                         headers=auth(key, user="owner"))
    assert revoke.status_code == 200 and revoke.json() == {"revoked": True}
    assert client.get(url(org, "members"), headers=alice).status_code == 404
    events = client.get(url(org, "audit"), headers=auth(key, user="owner"))
    assert [e["action"] for e in reversed(events.json()["events"])] == [
        "invitation.created", "invitation.accepted", "membership.revoked"]
    with engine.connect() as conn:
        assert len(conn.execute(select(team_audit_events)).all()) == 3
        assert {r.subject for r in conn.execute(select(organization_memberships)).all()} == {"owner"}


def test_server_membership_not_jwt_claims_controls_privileges(team_setup):
    client, key, _, org = team_setup
    outsider = auth(key, user="mallory", role="owner", permissions=["*"],
                    organization_id=org)
    assert client.post(url(org, "invitations"),
                       json={"target_subject": "attacker", "role": "admin"},
                       headers=outsider).status_code == 404
    assert client.get(url(org, "members"), headers=outsider).status_code == 404
    assert client.get(url(org, "audit"), headers=outsider).status_code == 404
    assert client.get("/api/v1/invitations", headers=outsider).json() == {"invitations": []}
    assert client.post(url(str(uuid4()), "invitations"),
                       json={"target_subject": "someone", "role": "viewer"},
                       headers=auth(key, user="owner")).status_code == 404
    assert client.post(url(org, "members/revoke"),
                       json={"target_subject": "owner"}, headers=outsider).status_code == 404


def test_owner_admin_role_ceiling_and_no_self_revocation(team_setup):
    client, key, _, org = team_setup
    assert invite(client, key, org, "admin", "admin").status_code == 201
    admin_invite = client.get("/api/v1/invitations",
                              headers=auth(key, user="admin")).json()["invitations"][0]["id"]
    assert client.post(f"/api/v1/invitations/{admin_invite}/accept",
                       headers=auth(key, user="admin")).status_code == 200
    assert invite(client, key, org, "elevated", "admin", actor="admin").status_code == 404
    assert invite(client, key, org, "owner2", "owner", actor="owner").status_code == 404
    assert invite(client, key, org, "staff", "developer", actor="admin").status_code == 201
    assert client.post(url(org, "members/revoke"), json={"target_subject": "admin"},
                       headers=auth(key, user="admin")).status_code == 404
    assert client.post(url(org, "members/revoke"), json={"target_subject": "owner"},
                       headers=auth(key, user="admin")).status_code == 404
    assert client.post(url(org, "members/revoke"), json={"target_subject": "owner"},
                       headers=auth(key, user="owner")).status_code == 404


@pytest.mark.parametrize("payload", [
    {"target_subject": "alice", "role": "owner"},
    {"target_subject": "alice", "role": "superadmin"},
    {"target_subject": " alice", "role": "viewer"},
    {"target_subject": "alice\n", "role": "viewer"},
    {"target_subject": "", "role": "viewer"},
    {"target_subject": "x" * 256, "role": "viewer"},
    {"target_subject": "alice", "role": "viewer", "organization_id": "fake"},
    {"target_subject": "alice", "role": "viewer", "issuer": "https://evil"},
    {"target_subject": 3, "role": "viewer"},
])
def test_invite_payload_cannot_inject_identity_or_permissions(team_setup, payload):
    client, key, engine, org = team_setup
    assert client.post(url(org, "invitations"), json=payload,
                       headers=auth(key, user="owner")).status_code in (404, 422)
    with engine.connect() as conn:
        assert conn.execute(select(team_audit_events)).all() == []


def test_auth_required_and_unconfigured_store_fail_closed(team_setup):
    client, key, _, org = team_setup
    assert client.post(url(org, "invitations"),
                       json={"target_subject": "alice", "role": "viewer"}).status_code == 401
    assert client.get("/api/v1/invitations").status_code == 401
    invalid = {"Authorization": "Bearer invalid"}
    assert client.get("/api/v1/invitations", headers=invalid).status_code == 401
    # The default application never creates or seeds team database tables.
    absent = TestClient(create_app())
    assert absent.get("/api/v1/invitations").status_code == 503
    assert absent.post("/api/v1/invitations/" + str(uuid4()) + "/accept").status_code == 503
