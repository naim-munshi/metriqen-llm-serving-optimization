"""HTTP identity contract tests; ephemeral signing keys, no external IdP."""
from datetime import datetime, timedelta, timezone
import base64
import json

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings, load_identity_settings

ISSUER = "https://login.example.test/realms/metriqen"
AUDIENCE = "metriqen-control-api"
AZP = "metriqen-web"


def b64_int(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.fixture(scope="module")
def fixture_keys():
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alternate = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    def jwk(private, kid):
        numbers = private.public_key().public_numbers()
        return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
                "n": b64_int(numbers.n), "e": b64_int(numbers.e)}
    return signing, alternate, jwk(signing, "key-1"), jwk(alternate, "key-2")


@pytest.fixture
def configured(fixture_keys):
    primary, rotated, first, second = fixture_keys
    settings = PinnedIdentitySettings(issuer=ISSUER, audience=AUDIENCE,
                                      authorized_party=AZP, trusted_jwks={"keys": [first]})
    return TestClient(create_app(settings)), primary, rotated, first, second


def issue(private, *, kid="key-1", typ="at+jwt", **updates):
    now = datetime.now(timezone.utc)
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-a",
              "azp": AZP, "iat": now - timedelta(seconds=10),
              "nbf": now - timedelta(seconds=10), "exp": now + timedelta(minutes=5)}
    claims.update(updates)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid, "typ": typ})


def response(client, token):
    return client.get("/api/v1/identity", headers={"Authorization": f"Bearer {token}"})


def test_unconfigured_route_returns_503_and_health_stays_live():
    client = TestClient(create_app())
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    result = client.get("/api/v1/identity")
    assert result.status_code == 503
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404


def test_valid_access_jwt_returns_only_verified_identity(configured):
    client, primary, *_ = configured
    token = issue(primary, role="owner", organization_id="attacker-org", projects=["fake"])
    result = response(client, token)
    assert result.status_code == 200
    assert result.json() == {"subject": "user-a", "issuer": ISSUER, "audience": AUDIENCE}
    assert token not in result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("value", ["", "Basic abc", "Bearer", "Bearer ",
                                    "Bearer a b", "Bearer  a", "Bearer\tbad"])
def test_missing_or_malformed_authorization_is_401(configured, value):
    client = configured[0]
    headers = {"Authorization": value} if value else {}
    result = client.get("/api/v1/identity", headers=headers)
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    assert "a b" not in result.text


def test_case_insensitive_bearer_scheme_is_supported(configured):
    client, primary, *_ = configured
    result = client.get("/api/v1/identity", headers={"Authorization": "bearer " + issue(primary)})
    assert result.status_code == 200


def test_duplicate_authorization_headers_rejected(configured):
    client, primary, *_ = configured
    token = issue(primary)
    result = client.get("/api/v1/identity", headers=[
        ("authorization", f"Bearer {token}"),
        ("authorization", f"Bearer {token}"),
    ])
    assert result.status_code == 401


@pytest.mark.parametrize("alter", [
    {"iss": "https://attacker.test"}, {"aud": "other-api"},
    {"azp": "untrusted-client"}, {"sub": ""},
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
    {"nbf": datetime.now(timezone.utc) + timedelta(minutes=5)},
    {"aud": [AUDIENCE, "other-audience"]},
])
def test_wrong_claims_rejected(configured, alter):
    client, primary, *_ = configured
    result = response(client, issue(primary, **alter))
    assert result.status_code == 401
    assert "invalid bearer access token" == result.json()["detail"]


def test_generic_jwt_type_is_rejected_even_if_signed(configured):
    client, primary, *_ = configured
    assert response(client, issue(primary, typ="JWT")).status_code == 401
    assert response(client, issue(primary, typ="id+jwt")).status_code == 401


def test_wrong_signer_and_unknown_kid_rejected(configured):
    client, primary, rotated, *_ = configured
    assert response(client, issue(rotated)).status_code == 401
    assert response(client, issue(primary, kid="unexpected")).status_code == 401


def test_pinned_key_rotation_only_when_operator_updates_snapshot(configured):
    client, _, rotated, first, second = configured
    token = issue(rotated, kid="key-2")
    assert response(client, token).status_code == 401
    rotated_client = TestClient(create_app(PinnedIdentitySettings(
        issuer=ISSUER, audience=AUDIENCE, authorized_party=AZP,
        trusted_jwks={"keys": [first, second]},
    )))
    assert response(rotated_client, token).status_code == 200


def test_tampered_token_does_not_leak_details(configured):
    client, primary, *_ = configured
    token = issue(primary)
    broken = token[:-8] + "aaaaaaaa"
    result = response(client, broken)
    assert result.status_code == 401
    assert broken not in result.text
    assert ISSUER not in result.text


def test_missing_env_is_fail_closed():
    assert load_identity_settings({}) is None
    with pytest.raises(ValueError, match="incomplete"):
        load_identity_settings({"METRIQEN_OIDC_ISSUER": ISSUER})


def test_valid_trusted_jwks_file_loads_without_network(tmp_path, fixture_keys):
    _, _, first, _ = fixture_keys
    path = tmp_path / "trusted.json"
    path.write_text(json.dumps({"keys": [first]}))
    values = {"METRIQEN_OIDC_ISSUER": ISSUER,
              "METRIQEN_OIDC_AUDIENCE": AUDIENCE,
              "METRIQEN_OIDC_AUTHORIZED_PARTY": AZP,
              "METRIQEN_OIDC_JWKS_PATH": str(path)}
    settings = load_identity_settings(values)
    assert settings is not None
    assert settings.trusted_jwks["keys"][0]["kid"] == "key-1"


@pytest.mark.parametrize("change", [
    {"kid": ""}, {"kty": "EC"}, {"alg": "none"}, {"d": "private"},
])
def test_malformed_trusted_jwks_rejected(tmp_path, fixture_keys, change):
    _, _, first, _ = fixture_keys
    path = tmp_path / "trusted.json"
    path.write_text(json.dumps({"keys": [{**first, **change}]}))
    values = {"METRIQEN_OIDC_ISSUER": ISSUER, "METRIQEN_OIDC_AUDIENCE": AUDIENCE,
              "METRIQEN_OIDC_AUTHORIZED_PARTY": AZP, "METRIQEN_OIDC_JWKS_PATH": str(path)}
    with pytest.raises(ValueError):
        load_identity_settings(values)


def test_duplicate_kid_in_file_rejected(tmp_path, fixture_keys):
    _, _, first, _ = fixture_keys
    path = tmp_path / "trusted.json"
    path.write_text(json.dumps({"keys": [first, first]}))
    values = {"METRIQEN_OIDC_ISSUER": ISSUER, "METRIQEN_OIDC_AUDIENCE": AUDIENCE,
              "METRIQEN_OIDC_AUTHORIZED_PARTY": AZP, "METRIQEN_OIDC_JWKS_PATH": str(path)}
    with pytest.raises(ValueError):
        load_identity_settings(values)


def test_oversized_trusted_jwks_rejected(tmp_path):
    path = tmp_path / "large.json"
    path.write_text("x" * (64 * 1024 + 1))
    with pytest.raises(ValueError):
        load_identity_settings({"METRIQEN_OIDC_ISSUER": ISSUER,
                                "METRIQEN_OIDC_AUDIENCE": AUDIENCE,
                                "METRIQEN_OIDC_AUTHORIZED_PARTY": AZP,
                                "METRIQEN_OIDC_JWKS_PATH": str(path)})
