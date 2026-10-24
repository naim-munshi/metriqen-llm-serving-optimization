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
from app.storage.workspaces import SQLWorkspaceStore
from app.storage.team import SQLTeamStore
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
        workspace_store=SQLWorkspaceStore(engine), team_store=SQLTeamStore(engine),
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
    assert "refresh_token" not in str(cache.values)
    assert "password" not in str(cache.values)
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
    assert "refresh_token" not in str(cache.values)

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


def test_bff_session_outage_does_not_log_out_or_silently_reauthorize(fixture):
    client, cache, _, _, _ = fixture
    complete_login(fixture)
    active = client.get("/auth/session").json()
    assert active["authenticated"] is True
    csrf = active["csrf"]
    assert client.get("/auth/organizations").status_code == 200

    cache.fail = True
    denied = client.get("/auth/organizations")
    assert denied.status_code == 503
    assert "secret test redis backend" not in denied.text
    assert client.post("/auth/logout", headers=browser_headers(csrf)).status_code == 503

    cache.fail = False
    # Recovered Redis does not permit unprotected logout, and a successfully
    # revoked ticket cannot be replayed using a previously valid CSRF.
    assert client.get("/auth/session").json()["authenticated"] is True
    assert client.post("/auth/logout").status_code == 403
    assert client.post("/auth/logout", headers=browser_headers(csrf)).status_code == 204
    assert client.get("/auth/session").json() == {"authenticated": False}
    assert client.get("/auth/organizations").status_code == 401
    assert client.post("/auth/logout", headers=browser_headers(csrf)).status_code == 401


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
    assert 'href="http://127.0.0.1:8081/console"' in response.text
    assert 'rel="noopener noreferrer"' in response.text
    assert 'does not share this browser session' in response.text
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



def test_signed_in_browser_workspace_create_and_projects_enforce_csrf(fixture):
    client, cache, _, engine, _ = fixture
    complete_login(fixture)
    csrf = client.get("/auth/session").json()["csrf"]
    headers = browser_headers(csrf)
    assert client.get("/auth/organizations").json() == {"organizations": []}
    assert client.post("/auth/organizations", json={"name": "Rejected"}).status_code == 403
    response = client.post("/auth/organizations", json={"name": "Research"},
                           headers=headers)
    assert response.status_code == 201
    org = response.json()
    assert org["name"] == "Research" and org["role"] == "owner"
    assert client.get("/auth/organizations").json()["organizations"] == [org]
    uri = f"/auth/organizations/{org['id']}/projects"
    assert client.post(uri, json={"name": "No CSRF"}).status_code == 403
    assert client.get(uri).json() == {"projects": []}
    made = client.post(uri, json={"name": "Model serving"}, headers=headers)
    assert made.status_code == 201
    assert client.get(uri).json()["projects"] == [made.json()]
    assert client.get("/auth/organizations/00000000-0000-4000-8000-000000000001/projects"
                      ).status_code == 404
    assert client.post(uri, json={"name": "P", "role": "owner"},
                       headers=headers).status_code == 422
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/organizations").status_code == 401
    assert client.post("/auth/organizations", json={"name": "Bad"},
                       headers=headers).status_code == 401


def test_account_ui_exposes_workspace_controls_as_safe_dom_text(fixture):
    client, _, _, _, _ = fixture
    page = client.get("/console/account").text
    script = client.get("/console/account/app.js").text
    assert 'id="organization-form"' in page
    assert 'id="workspace-list"' in page
    assert '"/auth/organizations"' in script
    assert 'document.createElement(tag)' in script
    assert "innerHTML" not in script
    assert "localStorage" not in script


def test_google_provider_exact_endpoints_secret_and_no_cross_origin_override(fixture):
    """Google endpoint exception never permits arbitrary OAuth hosts."""
    from dataclasses import replace
    from app.browser_auth import (
        GOOGLE_ISSUER, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL,
    )
    _, _, _, _, local_identity = fixture
    identity = replace(local_identity, issuer=GOOGLE_ISSUER)
    google = BrowserAuthSettings(
        authorization_url=GOOGLE_AUTH_URL, token_url=GOOGLE_TOKEN_URL,
        client_id=CLIENT, client_secret="synthetic-google-client-secret-123",
        identity=identity, provider="google",
    )
    for changes in (
        {"authorization_url": "https://attacker.example.test/authorize"},
        {"authorization_url": GOOGLE_AUTH_URL + "?redirect=attacker"},
        {"token_url": "https://evil.example.test/token"},
        {"token_url": GOOGLE_TOKEN_URL + "#fragment"},
        {"client_secret": None},
        {"provider": "not-google"},
    ):
        with pytest.raises(ValueError):
            replace(google, **changes)
    with pytest.raises(ValueError):
        replace(google, identity=local_identity)
    # Existing generic dedicated-jwt path retains issuer-origin restriction.
    with pytest.raises(ValueError):
        replace(google, provider="dedicated-jwt")


def test_google_id_token_bff_login_with_opaque_google_access_token(fixture):
    """Offline synthetic Google OIDC flow: opaque OAuth access != API JWT."""
    from dataclasses import replace
    from app.browser_auth import (
        GOOGLE_ISSUER, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL,
    )
    _, _, _, _, local_identity = fixture
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = private.public_key().public_numbers()
    identity = replace(
        local_identity, issuer=GOOGLE_ISSUER,
        trusted_jwks={"keys": [{
            "kty": "RSA", "kid": "google-synthetic-kid", "alg": "RS256",
            "use": "sig", "n": to_b64(pub.n), "e": to_b64(pub.e),
        }]},
    )
    opts = BrowserAuthSettings(
        authorization_url=GOOGLE_AUTH_URL, token_url=GOOGLE_TOKEN_URL,
        client_id=CLIENT, client_secret="synthetic-google-client-secret-123",
        identity=identity, provider="google",
    )
    cache = FakeRedis()

    class GoogleFixtureExchange:
        wrong_nonce = False
        wrong_issuer = False

        def exchange(self, code, verifier):
            assert code == "synthetic-google-code" and len(verifier) > 43
            assert cache.consumed is not None
            now = datetime.now(timezone.utc)
            body = {
                "iss": "https://attacker.example.test" if self.wrong_issuer else GOOGLE_ISSUER,
                "sub": "google-stable-subject-123", "aud": CLIENT,
                "nonce": "tampered" if self.wrong_nonce else cache.consumed["nonce"],
                "iat": now - timedelta(seconds=5),
                "exp": now + timedelta(minutes=5),
            }
            return {
                "id_token": jwt.encode(body, private, algorithm="RS256",
                                       headers={"kid": "google-synthetic-kid", "typ": "JWT"}),
                "access_token": "opaque-google-oauth-token-not-for-metriqen",
            }

    exchange = GoogleFixtureExchange()
    login = BrowserLogin(opts, BrowserSessionStore(cache), exchange)
    client = TestClient(create_app(identity, browser_login=login),
                        client=("127.0.0.1", 50600))
    start = client.get("/auth/login", follow_redirects=False)
    assert start.status_code == 302
    auth_url = urlsplit(start.headers["location"])
    assert auth_url.scheme == "https" and auth_url.netloc == "accounts.google.com"
    params = parse_qs(auth_url.query)
    assert params["code_challenge_method"] == ["S256"]
    state = params["state"][0]
    callback = client.get("/auth/callback", params={"code": "synthetic-google-code",
                                                    "state": state}, follow_redirects=False)
    assert callback.status_code == 303
    session = client.get("/auth/session").json()
    assert session["authenticated"] is True
    assert session["subject"] == "google-stable-subject-123"
    assert session["issuer"] == GOOGLE_ISSUER
    assert "opaque-google-oauth-token" not in str(cache.values)
    # A Google ID token remains disallowed as the distinct API bearer JWT.
    id_token = exchange.exchange("synthetic-google-code", "v"*48)["id_token"]
    assert client.get("/api/v1/identity",
                      headers={"Authorization": "Bearer " + id_token}).status_code == 401
    assert client.get("/auth/callback", params={"code": "synthetic-google-code",
                                                "state": state}).status_code == 401
    assert client.post("/auth/logout", headers=browser_headers(session["csrf"])).status_code == 204
    assert client.get("/auth/session").json() == {"authenticated": False}

    for wrong_field in ("wrong_nonce", "wrong_issuer"):
        setattr(exchange, wrong_field, True)
        start = client.get("/auth/login", follow_redirects=False)
        bad_state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        bad = client.get("/auth/callback", params={"code": "synthetic-google-code",
                                                  "state": bad_state}, follow_redirects=False)
        assert bad.status_code == 401
        assert client.get("/auth/session").json() == {"authenticated": False}
        setattr(exchange, wrong_field, False)


def test_google_provider_configuration_is_explicit(fixture, monkeypatch):
    from dataclasses import replace
    from app.browser_auth import GOOGLE_ISSUER, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL
    _, _, _, _, original = fixture
    identity = replace(original, issuer=GOOGLE_ISSUER)
    config = {
        "METRIQEN_BROWSER_PROVIDER": "google",
        "METRIQEN_BROWSER_AUTH_URL": GOOGLE_AUTH_URL,
        "METRIQEN_BROWSER_TOKEN_URL": GOOGLE_TOKEN_URL,
        "METRIQEN_BROWSER_REDIS_URL":
            "redis://session_user:at-least-16-bytes-of-secret@127.0.0.1:6379/0",
        "METRIQEN_BROWSER_CLIENT_SECRET": "synthetic-google-client-secret-123",
    }
    import app.browser_auth as auth_module
    monkeypatch.setattr(auth_module.redis.Redis, "from_url",
                        lambda *_args, **_kwargs: FakeRedis())
    google = browser_configured(config, identity)
    assert google.settings.provider == "google"
    assert browser_configured({}, identity) is None
    for bad in (
        {**config, "METRIQEN_BROWSER_CLIENT_SECRET": ""},
        {**config, "METRIQEN_BROWSER_TOKEN_URL": "https://attacker.test/token"},
        {"METRIQEN_BROWSER_PROVIDER": "google"},
    ):
        with pytest.raises(ValueError):
            browser_configured(bad, identity)
