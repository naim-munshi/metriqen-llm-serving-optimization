"""Browser team BFF contract: real session boundary, synthetic OIDC and Redis.

No external IdP login, Redis server or production TLS is claimed.
"""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.storage.schema import organizations, organization_memberships, team_audit_events
from app.security.policy import Role
from test_browser_auth import (
    fixture as browser_fixture, complete_login, browser_headers, LOCAL_ORIGIN,
)


def test_browser_invites_require_session_csrf_and_database_permissions(browser_fixture):
    client, _, _, engine, _ = browser_fixture
    complete_login(browser_fixture)
    csrf = client.get("/auth/session").json()["csrf"]
    secure = browser_headers(csrf)
    org_r = client.post("/auth/organizations", json={"name": "Team"}, headers=secure)
    assert org_r.status_code == 201
    org = org_r.json()["id"]
    endpoint = f"/auth/organizations/{org}/invitations"

    assert client.post(endpoint, json={"target_subject": "alice", "role": "viewer"}).status_code == 403
    assert client.post(endpoint, json={"target_subject": "alice", "role": "viewer"},
                       headers={"Origin": "https://evil.example",
                                "X-Metriqen-CSRF": csrf}).status_code == 403
    assert client.post(endpoint, json={"target_subject": "alice", "role": "viewer"},
                       headers={"Origin": LOCAL_ORIGIN, "X-Metriqen-CSRF": csrf,
                                "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(endpoint, json={"target_subject": "alice", "role": "owner"},
                       headers=secure).status_code == 404
    assert client.post(endpoint, json={"target_subject": "alice", "role": "viewer",
                                       "role_claim": "owner"}, headers=secure).status_code == 422

    issued = client.post(endpoint, json={"target_subject": "alice", "role": "viewer"},
                         headers=secure)
    assert issued.status_code == 201
    manager_pending = client.get(f"/auth/organizations/{org}/invitations")
    assert manager_pending.status_code == 200
    assert [i["id"] for i in manager_pending.json()["invitations"]] == [issued.json()["id"]]
    cancel_url = f"/auth/organizations/{org}/invitations/{issued.json()['id']}/cancel"
    assert client.post(cancel_url).status_code == 403
    assert client.post(cancel_url, headers={"Origin": "https://evil.example",
                       "X-Metriqen-CSRF": csrf}).status_code == 403
    assert client.post(cancel_url, headers=secure).json() == {"cancelled": True}
    assert client.post(cancel_url, headers=secure).status_code == 404
    assert client.get(f"/auth/organizations/{org}/invitations").json()["invitations"] == []
    assert client.get(f"/auth/organizations/{org}/members").status_code == 200
    assert client.get(f"/auth/organizations/{org}/audit").json()["events"][0]["action"] == "invitation.cancelled"
    assert client.get("/auth/invitations").json()["invitations"] == []
    assert client.post(f"/auth/invitations/{issued.json()['id']}/accept",
                       headers=secure).status_code == 404
    with engine.connect() as conn:
        assert len(conn.execute(select(team_audit_events)).all()) == 2


def test_browser_pending_accept_is_one_time_and_logout_revokes_access(browser_fixture):
    client, _, _, engine, _ = browser_fixture
    owner, org = "trusted-owner", uuid4()
    issuer = "https://idp.example.test/realms/metriqen"
    with engine.begin() as conn:
        conn.execute(insert(organizations).values(id=org, name="Team"))
        conn.execute(insert(organization_memberships).values(
            issuer=issuer, subject=owner, organization_id=org, role="owner"))
    from app.storage.team import SQLTeamStore
    from app.security.oidc import VerifiedIdentity
    invitation = SQLTeamStore(engine).invite(
        VerifiedIdentity(issuer=issuer, subject=owner, audience="metriqen-control-api"),
        org, "person-123", Role.VIEWER,
    )
    complete_login(browser_fixture)
    csrf = client.get("/auth/session").json()["csrf"]
    token = browser_headers(csrf)
    pending = client.get("/auth/invitations").json()["invitations"]
    assert len(pending) == 1 and pending[0]["id"] == invitation["id"]
    assert client.post(f"/auth/invitations/{invitation['id']}/accept").status_code == 403
    assert client.post(f"/auth/invitations/{invitation['id']}/accept",
                       headers=token).status_code == 200
    assert client.post(f"/auth/invitations/{invitation['id']}/accept",
                       headers=token).status_code == 404
    assert client.get("/auth/invitations").json()["invitations"] == []
    assert client.get("/auth/organizations").json()["organizations"][0]["role"] == "viewer"
    assert client.get(f"/auth/organizations/{org}/members").status_code == 404
    assert client.post(f"/auth/organizations/{org}/invitations",
                       json={"target_subject": "evil", "role": "admin"},
                       headers=token).status_code == 404
    assert client.post("/auth/logout", headers=token).status_code == 204
    assert client.get("/auth/invitations").status_code == 401
    assert client.get(f"/auth/organizations/{org}/audit").status_code == 401


def test_team_browser_routes_not_mounted_without_explicit_oidc(browser_fixture):
    client, _, _, _, _ = browser_fixture
    remote = TestClient(client.app, client=("192.0.2.123", 40000))
    assert remote.get("/auth/invitations").status_code == 403
    assert remote.get("/auth/organizations/" + str(uuid4()) + "/members").status_code == 403
    assert client.get("/console/account").status_code == 200
    js = client.get("/console/account/app.js")
    assert js.status_code == 200
    assert "textContent" in js.text
    assert "/auth/invitations" in js.text
    assert "innerHTML" not in js.text
