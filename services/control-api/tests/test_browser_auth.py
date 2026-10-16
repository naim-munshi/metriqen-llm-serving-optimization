"""OIDC Authorization Code + PKCE, Redis-backed BFF and session security tests.

All signing keys and IdP responses are synthetic; no public IdP, network,
browser automation or real Redis is contacted.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.browser_auth import (
    BrowserAuthSettings, BrowserLogin, BrowserSessionStore, FLOW_COOKIE,
    FLOW_SECONDS, LOCAL_ORIGIN, SESSION_COOKIE, browser_configured,
)
from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.storage.profiles import SQLProfileStore
from app.storage.schema import metadata

ISSUER = "https://idp.example.test/realms/metriqen"
CLIENT = "metriqen-web"
AUD = "metriqen-control-api"


def to_b64(value):
    return base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode().rstrip("=")


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.consumed = None
        self.fail = False

    def set(self, key, value, *, ex, nx):
        assert nx is True and ex in (300, 1800)
        if self.fail:
            raise ConnectionError("secret test redis backend")
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def getdel(self, key):
        if self.fail:
            raise ConnectionError("secret test redis backend")
        value = self.values.pop(key, None)
        if value:
            self.consumed = json.loads(value)
        return value

    def get(self, key):
        if self.fail:
            raise ConnectionError("secret test redis backend")
        return self.values.get(key)

    def delete(self, key):
        if self.fail:
            raise ConnectionError("secret test redis backend")
        self.values.pop(key, None)
        return 1


class FakeIdP:
    def __init__(self, signing, cache):
        self.key = signing
        self.cache = cache
        self.nonce_wrong = False
        self.subject_mismatch = False
        self.id_typ = "JWT"
        self.calls = []
        self.wrong_issuer = False

    def exchange(self, code, verifier):
        self.calls.append((code, verifier))
        assert len(verifier) > 43
        if code == "reject":
            raise ValueError("test token endpoint failure")
        now = datetime.now(timezone.utc)
        claims = {
            "iss": "https://attacker.example.test" if self.wrong_issuer else ISSUER,
            "aud": CLIENT, "sub": "person-123",
            "nonce": "wrong-nonce" if self.nonce_wrong else self.cache.consumed["nonce"],
            "iat": now - timedelta(seconds=5), "exp": now + timedelta(minutes=5),
        }
        access = {
            "iss": ISSUER, "aud": AUD,
            "azp": CLIENT,
            "sub": "attacker" if self.subject_mismatch else "person-123",
            "iat": now - timedelta(seconds=5),
            "nbf": now - timedelta(seconds=5),
            "exp": now + timedelta(minutes=5),
        }
        id_token = jwt.encode(claims, self.key, algorithm="RS256",
                              headers={"kid": "signed", "typ": self.id_typ})
        access_token = jwt.encode(access, self.key, algorithm="RS256",
                                  headers={"kid": "signed", "typ": "at+jwt"})
        return {"id_token": id_token, "access_token": access_token,
                "refresh_token": "never-store-this-token"}


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.delenv("METRIQEN_BROWSER_AUTH_URL", raising=False)
    monkeypatch.delenv("METRIQEN_BROWSER_TOKEN_URL", raising=False)
    monkeypatch.delenv("METRIQEN_BROWSER_REDIS_URL", raising=False)
    monkeypatch.delenv("METRIQEN_BROWSER_CLIENT_SECRET", raising=False)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = private.public_key().public_numbers()
    identity = PinnedIdentitySettings(
        issuer=ISSUER, audience=AUD, authorized_party=CLIENT,
        trusted_jwks={"keys": [{
            "kty": "RSA", "kid": "signed", "alg": "RS256", "use": "sig",
            "n": to_b64(pub.n), "e": to_b64(pub.e),
        }]},
    )
    opts = BrowserAuthSettings(
        authorization_url=ISSUER + "/protocol/openid-connect/auth",
        token_url=ISSUER + "/protocol/openid-connect/token",
        client_id=CLIENT, client_secret=None, identity=identity,
    )
    store = FakeRedis()
    exchanger = FakeIdP(private, store)
    login = BrowserLogin(opts, BrowserSessionStore(store), exchanger)
    engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    metadata.create_all(engine)
    client = TestClient(create_app(
        identity, profile_store=SQLProfileStore(engine), browser_login=login,
    ), client=("127.0.0.1", 50505))
    try:
        yield client, store, exchanger, engine, identity
    finally:
        engine.dispose()


def start_login(client):
    result = client.get("/auth/login", follow_redirects=False)
    assert result.status_code == 302
    uri = urlsplit(result.headers["location"])
    params = parse_qs(uri.query)
    assert uri.scheme == "https" and uri.hostname == "idp.example.test"
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid"]
    assert params["redirect_uri"] == [LOCAL_ORIGIN + "/auth/callback"]
    assert FLOW_COOKIE in client.cookies
    return params


def finish_login(client, params, *, code="fake-authorization-code"):
    return client.get("/auth/callback",
                      params={"state": params["state"][0], "code": code},
                      follow_redirects=False)


def complete_login(fixture):
    client, cache, _, _, _ = fixture
    params = start_login(client)
    unconsumed = json.loads(cache.values[
        BrowserSessionStore.key("flow", params["state"][0])
    ])
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(unconsumed["verifier"].encode()).digest()
    ).decode().rstrip("=")
    assert params["code_challenge"] == [challenge]
    assert params["nonce"] == [unconsumed["nonce"]]
    response = finish_login(client, params)
    assert response.status_code == 303
    assert response.headers["location"] == "/console/account"
    assert SESSION_COOKIE in client.cookies
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    return response


def browser_headers(csrf):
    return {"Origin": LOCAL_ORIGIN, "X-Metriqen-CSRF": csrf,
            "Sec-Fetch-Site": "same-origin"}


def test_default_mode_exposes_no_auth_or_login_page(monkeypatch):
    monkeypatch.delenv("METRIQEN_BROWSER_AUTH_URL", raising=False)
    monkeypatch.delenv("METRIQEN_BROWSER_TOKEN_URL", raising=False)
    monkeypatch.delenv("METRIQEN_BROWSER_REDIS_URL", raising=False)
    assert browser_configured({}, None) is None
    client = TestClient(create_app())
    for path in ("/auth/login", "/auth/callback", "/auth/session", "/console/account"):
        assert client.get(path).status_code == 404


def test_full_bff_flow_profile_crud_csrf_logout_and_replay(fixture):
    client, cache, exchanger, _, _ = fixture
    assert client.get("/auth/session").json() == {"authenticated": False}
    params = start_login(client)
    payload = json.loads(cache.values[BrowserSessionStore.key("flow", params["state"][0])])
    assert "refresh_token" not in json.dumps(cache.values)
    assert "password" not in json.dumps(cache.values)
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(payload["verifier"].encode()).digest()
    ).decode().rstrip("=")
    assert params["code_challenge"] == [expected_challenge]

    signed_in = finish_login(client, params)
    assert signed_in.status_code == 303
    assert SESSION_COOKIE in client.cookies
    assert len(exchanger.calls) == 1
    active = client.get("/auth/session").json()
    assert active["authenticated"] is True
    assert active["subject"] == "person-123"
    assert active["issuer"] == ISSUER
    csrf = active["csrf"]
    assert len(csrf) > 32
    assert "refresh_token" not in json.dumps(cache.values)

    assert client.get("/auth/profile").status_code == 404
    assert client.post("/auth/profile", json={"display_name": "Alice"}).status_code == 403
    assert client.post("/auth/profile", json={"display_name": "Alice"},
                       headers={"Origin": "https://evil.example",
                                "X-Metriqen-CSRF": csrf}).status_code == 403
    assert client.post("/auth/profile", json={"display_name": "Alice"},
                       headers=browser_headers("invalid")).status_code == 403
    assert client.post("/auth/profile", json={"display_name": " Alice "},
                       headers=browser_headers(csrf)).status_code == 201
    assert client.get("/auth/profile").json()["display_name"] == "Alice"
    assert client.patch("/auth/profile", json={"display_name": "Alice 2"},
                        headers=browser_headers(csrf)).json()["display_name"] == "Alice 2"
    assert client.post("/auth/profile", json={"display_name": "duplicate"},
                       headers=browser_headers(csrf)).status_code == 409
    assert client.post("/auth/logout").status_code == 403
    logout = client.post("/auth/logout", headers=browser_headers(csrf))
    assert logout.status_code == 204
    assert client.get("/auth/session").json() == {"authenticated": False}
    assert client.get("/auth/profile").status_code == 401
    assert finish_login(client, params).status_code == 401
    assert len(exchanger.calls) == 1


def test_unknown_browser_binding_rejected_before_exchanging_code(fixture):
    client, _, exchanger, _, _ = fixture
    p = start_login(client)
    client.cookies.set(FLOW_COOKIE, "a" * 43)
    assert finish_login(client, p).status_code == 401
    assert not exchanger.calls


@pytest.mark.parametrize("invalid", ["nonce_wrong", "subject_mismatch", "wrong_issuer"])
def test_invalid_oidc_response_never_creates_session(fixture, invalid):
    client, _, exchanger, _, _ = fixture
    setattr(exchanger, invalid, True)
    p = start_login(client)
    assert finish_login(client, p).status_code == 401
    assert client.get("/auth/session").json() == {"authenticated": False}


def test_invalid_state_never_reaches_idp(fixture):
    client, _, exchanger, _, _ = fixture
    p = start_login(client)
    result = client.get("/auth/callback", params={"state": "bad", "code": "x"})
    assert result.status_code == 401
    assert exchanger.calls == []


def test_session_revocation_and_redis_outage_are_fail_closed(fixture):
    client, cache, _, _, _ = fixture
    complete_login(fixture)
    assert client.get("/auth/session").json()["authenticated"]
    cache.fail = True
    assert client.get("/auth/session").status_code == 503
    assert client.get("/auth/profile").status_code == 503


def test_local_only_auth_routes_reject_remote_clients(fixture):
    client, _, _, _, _ = fixture
    remote = TestClient(client.app, client=("192.0.2.22", 4333))
    assert remote.get("/auth/login").status_code == 403
    assert remote.get("/auth/session").status_code == 403


@pytest.mark.parametrize("changes", [
    {"authorization_url": "http://idp.example.test/auth"},
    {"authorization_url": "https://evil.example.test/auth"},
    {"authorization_url": "https://idp.example.test/auth?redirect=https://evil.example"},
    {"token_url": "https://idp.example.test/token#fragment"},
])
def test_browser_idp_endpoints_must_stay_exact_trusted_origin(fixture, changes):
    from dataclasses import replace
    _, _, _, _, identity = fixture
    settings = BrowserAuthSettings(
        authorization_url=ISSUER + "/auth", token_url=ISSUER + "/token",
        client_id=CLIENT, client_secret=None, identity=identity,
    )
    with pytest.raises(ValueError):
        replace(settings, **changes)


def test_account_console_asset_security_and_browser_token_hygiene(fixture):
    client, _, _, _, _ = fixture
    response = client.get("/console/account")
    assert response.status_code == 200
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert 'href="/console/account/app.css"' in response.text
    script = client.get("/console/account/app.js").text
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "innerHTML" not in script
    assert '"/auth/session"' in script
    assert '"/auth/profile"' in script
    assert '"/auth/logout"' in script


def test_redis_setup_requires_local_acl_and_complete_config(fixture, monkeypatch):
    _, _, _, _, identity = fixture
    base = {
        "METRIQEN_BROWSER_AUTH_URL": ISSUER + "/auth",
        "METRIQEN_BROWSER_TOKEN_URL": ISSUER + "/token",
        "METRIQEN_BROWSER_REDIS_URL":
            "redis://session_user:at-least-16-bytes-of-secret@127.0.0.1:6379/0",
    }
    import app.browser_auth as auth_module
    seen = []
    monkeypatch.setattr(auth_module.redis.Redis, "from_url",
                        lambda url, **kwargs: (seen.append(url) or FakeRedis()))
    assert browser_configured(base, identity) is not None
    assert len(seen) == 1
    for bad in [
        {**base, "METRIQEN_BROWSER_REDIS_URL": "redis://127.0.0.1:6379/0"},
        {**base, "METRIQEN_BROWSER_REDIS_URL":
            "redis://session_user:at-least-16-bytes-of-secret@public.example:6379/0"},
        {**base, "METRIQEN_BROWSER_TOKEN_URL": "https://evil.example.test/token"},
        {"METRIQEN_BROWSER_AUTH_URL": ISSUER + "/auth"},
    ]:
        with pytest.raises(ValueError):
            browser_configured(bad, identity)
