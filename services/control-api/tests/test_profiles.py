"""Offline verified-identity account API, persistence and isolation tests."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.storage.profiles import SQLProfileStore
from app.storage.schema import metadata


ISSUER_A = "https://idp-a.example.test/realms/metriqen"
ISSUER_B = "https://idp-b.example.test/realms/metriqen"
AUDIENCE = "metriqen-control-api"
AZP = "metriqen-web"


def to_b64(value: int) -> str:
    return base64.urlsafe_b64encode(
        value.to_bytes((value.bit_length() + 7) // 8, "big")
    ).decode("ascii").rstrip("=")


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_TOKEN", raising=False)
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_DB", raising=False)
    engine = create_engine(
        "sqlite+pysqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    metadata.create_all(engine)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    settings = PinnedIdentitySettings(
        issuer=ISSUER_A, audience=AUDIENCE, authorized_party=AZP,
        trusted_jwks={"keys": [{
            "kty": "RSA", "kid": "account-tests", "alg": "RS256", "use": "sig",
            "n": to_b64(pub.n), "e": to_b64(pub.e),
        }]},
    )
    try:
        yield TestClient(create_app(settings, profile_store=SQLProfileStore(engine))), key, engine
    finally:
        engine.dispose()


def jwt_token(key, *, issuer=ISSUER_A, subject="alice", typ="at+jwt", **extra):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer, "aud": AUDIENCE, "azp": AZP, "sub": subject,
        "iat": now - timedelta(seconds=5),
        "nbf": now - timedelta(seconds=5),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(extra)
    return jwt.encode(claims, key, algorithm="RS256",
                      headers={"kid": "account-tests", "typ": typ})


def headers(key, **kwargs):
    return {"Authorization": "Bearer " + jwt_token(key, **kwargs)}


def test_account_flow_create_get_update_and_no_seed(sandbox):
    client, key, _ = sandbox
    auth = headers(key)
    assert client.get("/api/v1/me", headers=auth).status_code == 404
    created = client.post("/api/v1/me", json={"display_name": "  Alice  "}, headers=auth)
    assert created.status_code == 201
    payload = created.json()
    assert payload["issuer"] == ISSUER_A
    assert payload["subject"] == "alice"
    assert payload["display_name"] == "Alice"
    assert "created_at" in payload and "updated_at" in payload
    assert client.get("/api/v1/me", headers=auth).json() == payload
    assert client.post("/api/v1/me", json={"display_name": "another"}, headers=auth).status_code == 409
    updated = client.patch("/api/v1/me", json={"display_name": "New Alice"}, headers=auth)
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "New Alice"
    assert updated.json()["created_at"] == payload["created_at"]
    assert client.get("/api/v1/me", headers=auth).json()["display_name"] == "New Alice"
    assert updated.headers["cache-control"] == "no-store"


def test_different_subject_has_no_access_and_no_auto_registration(sandbox):
    client, key, _ = sandbox
    assert client.post("/api/v1/me", json={"display_name": "Alice"},
                       headers=headers(key)).status_code == 201
    assert client.get("/api/v1/me", headers=headers(key, subject="bob")).status_code == 404
    assert client.patch("/api/v1/me", json={"display_name": "Mallory"},
                        headers=headers(key, subject="bob")).status_code == 404
    assert client.get("/api/v1/me", headers=headers(key)).json()["display_name"] == "Alice"


def test_db_key_uses_issuer_and_subject_not_subject_alone(sandbox):
    client, key, engine = sandbox
    from app.security.oidc import VerifiedIdentity
    store = SQLProfileStore(engine)
    alternate = VerifiedIdentity(subject="alice", issuer=ISSUER_B, audience=AUDIENCE)
    store.create(alternate, "Bob at IdP B")
    assert client.get("/api/v1/me", headers=headers(key)).status_code == 404
    assert client.post("/api/v1/me", json={"display_name": "Alice at IdP A"},
                       headers=headers(key)).status_code == 201
    assert store.get(alternate)["display_name"] == "Bob at IdP B"


@pytest.mark.parametrize("body", [
    {}, {"display_name": ""}, {"display_name": "  "},
    {"display_name": "a" * 121}, {"display_name": 123},
    {"display_name": "name", "role": "owner"},
    {"display_name": "bad\nheader"},
    {"display_name": "bad\x00name"},
])
def test_rejects_untrusted_or_invalid_profile_input(sandbox, body):
    client, key, _ = sandbox
    assert client.post("/api/v1/me", json=body, headers=headers(key)).status_code == 422
    assert client.get("/api/v1/me", headers=headers(key)).status_code == 404


def test_signed_role_and_identity_claims_cannot_provision_admin(sandbox):
    client, key, _ = sandbox
    token = headers(key, role="owner", organization_id="other-org",
                    profile={"display_name": "Admin"}, user_id="other-user")
    result = client.post("/api/v1/me", json={"display_name": "Safe"}, headers=token)
    assert result.status_code == 201
    assert set(result.json()) == {
        "issuer", "subject", "display_name", "created_at", "updated_at",
    }
    assert result.json()["display_name"] == "Safe"
    assert result.json()["subject"] == "alice"


def test_invalid_auth_never_provisions_record(sandbox):
    client, key, _ = sandbox
    for auth in ({}, {"Authorization": "Bearer invalid"},
                 headers(key, typ="JWT"),
                 headers(key, issuer="https://attacker.example")):
        assert client.post("/api/v1/me", json={"display_name": "Hacker"},
                           headers=auth).status_code == 401
    assert client.get("/api/v1/me", headers=headers(key)).status_code == 404


def test_default_unconfigured_account_routes_fail_closed():
    client = TestClient(create_app())
    assert client.get("/api/v1/me").status_code == 503
    assert client.post("/api/v1/me", json={"display_name": "A"}).status_code == 503
    assert client.get("/health/live").status_code == 200


def test_verified_identity_without_database_returns_503(sandbox):
    _, key, _ = sandbox
    pub = key.public_key().public_numbers()
    settings = PinnedIdentitySettings(
        issuer=ISSUER_A, audience=AUDIENCE, authorized_party=AZP,
        trusted_jwks={"keys": [{
            "kty": "RSA", "kid": "account-tests", "alg": "RS256", "use": "sig",
            "n": to_b64(pub.n), "e": to_b64(pub.e),
        }]},
    )
    client = TestClient(create_app(settings))
    auth = headers(key)
    assert client.get("/api/v1/me", headers=auth).status_code == 503
    assert client.post("/api/v1/me", json={"display_name": "Alice"},
                       headers=auth).status_code == 503


def test_store_unavailable_does_not_echo_sensitive_diagnostics(sandbox):
    client, key, engine = sandbox
    engine.dispose()
    # SQLite StaticPool dispose removes schema: storage failure returns generic 503.
    response = client.post("/api/v1/me", json={"display_name": "Alice"},
                           headers=headers(key))
    assert response.status_code == 503
    assert "database" not in response.text.lower()
