"""Tenant-bound workspace authorization tests (synthetic OIDC signing, SQLite schema)."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.storage.schema import metadata, organizations, projects, organization_memberships, project_grants
from app.storage.workspaces import SQLWorkspaceStore

ISSUER = "https://workspace-idp.example.test/realms/metriqen"
AUDIENCE = "metriqen-control-api"
CLIENT_ID = "metriqen-web"


def b64(n):
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, "big")).decode().rstrip("=")


@pytest.fixture
def setup(monkeypatch):
    for name in ("METRIQEN_BROWSER_AUTH_URL", "METRIQEN_BROWSER_REDIS_URL",
                 "METRIQEN_BROWSER_TOKEN_URL", "METRIQEN_LOCAL_MODEL_TOKEN",
                 "METRIQEN_LOCAL_MODEL_DB"):
        monkeypatch.delenv(name, raising=False)
    engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    metadata.create_all(engine)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    settings = PinnedIdentitySettings(
        issuer=ISSUER, audience=AUDIENCE, authorized_party=CLIENT_ID,
        trusted_jwks={"keys": [{
            "kty": "RSA", "kid": "trusted", "alg": "RS256", "use": "sig",
            "n": b64(public.n), "e": b64(public.e),
        }]},
    )
    store = SQLWorkspaceStore(engine)
    client = TestClient(create_app(settings, workspace_store=store))
    try:
        yield client, key, engine, store, settings
    finally:
        engine.dispose()


def auth(key, user="alice", **claims):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER, "sub": user, "aud": AUDIENCE, "azp": CLIENT_ID,
        "iat": now-timedelta(seconds=3), "nbf": now-timedelta(seconds=3),
        "exp": now+timedelta(minutes=5),
    }
    payload.update(claims)
    token = jwt.encode(payload, key, algorithm="RS256",
                       headers={"kid": "trusted", "typ": "at+jwt"})
    return {"Authorization": "Bearer " + token}


def test_verified_user_self_provisions_org_and_owner_then_project(setup):
    client, key, engine, store, _ = setup
    headers = auth(key)
    assert client.get("/api/v1/organizations", headers=headers).json() == {"organizations": []}
    created = client.post("/api/v1/organizations", json={"name": "  Acme AI "}, headers=headers)
    assert created.status_code == 201
    org = created.json()
    UUID(org["id"])
    assert org["role"] == "owner" and org["name"] == "Acme AI"
    listed = client.get("/api/v1/organizations", headers=headers)
    assert listed.status_code == 200 and listed.json()["organizations"] == [org]
    created_project = client.post(
        f"/api/v1/organizations/{org['id']}/projects",
        json={"name": "LLM Deployment"}, headers=headers,
    )
    assert created_project.status_code == 201
    result = created_project.json()
    assert UUID(result["id"]) and result["organization_id"] == org["id"]
    assert client.get(
        f"/api/v1/organizations/{org['id']}/projects", headers=headers
    ).json()["projects"] == [result]
    with engine.connect() as conn:
        membership = conn.execute(select(organization_memberships)).one()
        assert membership.issuer == ISSUER and membership.subject == "alice"
        assert membership.role == "owner" and str(membership.organization_id) == org["id"]


def test_cross_tenant_org_and_project_are_invisible_even_with_forged_roles(setup):
    client, key, _, _, _ = setup
    alice = auth(key)
    bob = auth(key, user="bob", role="owner", organization_id="forged",
               permissions=["project.create", "owner"])
    org = client.post("/api/v1/organizations", json={"name": "Private"},
                      headers=alice).json()
    assert client.get("/api/v1/organizations", headers=bob).json() == {"organizations": []}
    assert client.get(f"/api/v1/organizations/{org['id']}/projects",
                      headers=bob).status_code == 404
    assert client.post(f"/api/v1/organizations/{org['id']}/projects",
                       json={"name": "Attack"}, headers=bob).status_code == 404
    assert client.get(f"/api/v1/organizations/{uuid4()}/projects",
                      headers=bob).status_code == 404
    assert client.get(f"/api/v1/organizations/{org['id']}/projects",
                      headers=alice).json()["projects"] == []


def test_membership_permissions_and_project_grant_visibility(setup):
    client, key, engine, _, _ = setup
    owner = auth(key)
    org = client.post("/api/v1/organizations", json={"name": "Team"},
                      headers=owner).json()
    project_a = client.post(f"/api/v1/organizations/{org['id']}/projects",
                            json={"name": "Visible"}, headers=owner).json()
    project_b = client.post(f"/api/v1/organizations/{org['id']}/projects",
                            json={"name": "Hidden"}, headers=owner).json()
    viewer = auth(key, user="viewer")
    with engine.begin() as conn:
        conn.execute(insert(organization_memberships).values(
            issuer=ISSUER, subject="viewer",
            organization_id=UUID(org["id"]), role="viewer",
        ))
        conn.execute(insert(project_grants).values(
            issuer=ISSUER, subject="viewer",
            organization_id=UUID(org["id"]), project_id=UUID(project_a["id"]),
        ))
    response = client.get(f"/api/v1/organizations/{org['id']}/projects", headers=viewer)
    assert response.status_code == 200
    assert response.json()["projects"] == [project_a]
    assert client.post(f"/api/v1/organizations/{org['id']}/projects",
                       json={"name": "Illegal"}, headers=viewer).status_code == 404
    with engine.begin() as conn:
        conn.execute(organization_memberships.delete().where(
            organization_memberships.c.subject == "viewer"
        ))
    assert client.get(f"/api/v1/organizations/{org['id']}/projects",
                      headers=viewer).status_code == 404
    assert client.get(f"/api/v1/organizations/{org['id']}/projects",
                      headers=owner).json()["projects"] == [project_b, project_a]


def test_auth_precedes_db_and_bad_input_rejected(setup):
    client, key, engine, _, _ = setup
    uri = "/api/v1/organizations"
    assert client.post(uri, json={"name": "Evil"}).status_code == 401
    assert client.post(uri, json={"name": "Evil"},
                       headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.post(uri, json={"name": "Evil", "role": "owner"},
                       headers=auth(key)).status_code == 422
    for body in ({}, {"name": ""}, {"name": "  "}, {"name": "A\nB"},
                 {"name": "x"*201}, {"name": 42}):
        assert client.post(uri, json=body, headers=auth(key)).status_code == 422
    assert client.get(uri, headers=auth(key)).json() == {"organizations": []}
    org = client.post(uri, json={"name": "Owned"}, headers=auth(key)).json()
    assert client.get(f"/api/v1/organizations/{org['id'].upper()}/projects",
                      headers=auth(key)).status_code == 404


def test_unconfigured_database_fails_closed(setup):
    _, key, _, _, settings = setup
    no_store = TestClient(create_app(settings))
    assert no_store.get("/api/v1/organizations", headers=auth(key)).status_code == 503
    assert no_store.post("/api/v1/organizations", json={"name": "None"},
                         headers=auth(key)).status_code == 503


def test_limits_are_generic_and_do_not_insert_resources(setup):
    client, key, engine, store, _ = setup
    store.MAX_ORGANIZATIONS = 0
    assert client.post("/api/v1/organizations", json={"name": "Overflow"},
                       headers=auth(key)).status_code == 409
    with engine.connect() as conn:
        assert conn.execute(select(organizations)).all() == []
    store.MAX_ORGANIZATIONS = 20
    org = client.post("/api/v1/organizations", json={"name": "Allowed"},
                      headers=auth(key)).json()
    store.MAX_PROJECTS_PER_ORGANIZATION = 0
    assert client.post(f"/api/v1/organizations/{org['id']}/projects",
                       json={"name": "Overflow"}, headers=auth(key)).status_code == 409
    with engine.connect() as conn:
        assert conn.execute(select(projects)).all() == []
