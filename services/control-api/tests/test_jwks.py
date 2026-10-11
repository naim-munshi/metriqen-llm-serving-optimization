"""Offline JWKS signing-key selection and rejection tests."""

import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.security.jwks import verify_access_token_from_jwks
from app.security.oidc import IdentityRejected

ISSUER = "https://login.example.test/realms/metriqen"
AUDIENCE = "metriqen-api"
CLIENT_ID = "metriqen-web"


def b64url_int(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.fixture(scope="module")
def material():
    primary = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_jwk(key, kid):
        numbers = key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "kid": kid,
            "alg": "RS256",
            "use": "sig",
            "n": b64url_int(numbers.n),
            "e": b64url_int(numbers.e),
        }

    return primary, rotated, public_jwk(primary, "key-1"), public_jwk(rotated, "key-2")


def claims(**updates):
    now = datetime.now(timezone.utc)
    value = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "person-123",
        "azp": CLIENT_ID,
        "iat": now - timedelta(seconds=10),
        "nbf": now - timedelta(seconds=10),
        "exp": now + timedelta(minutes=5),
    }
    value.update(updates)
    return value


def make_token(key, *, kid="key-1", headers=None, payload=None):
    return jwt.encode(
        payload or claims(), key, algorithm="RS256",
        headers={"kid": kid, **(headers or {})},
    )


def verify(token, keys, **overrides):
    return verify_access_token_from_jwks(
        token,
        trusted_jwks={"keys": keys},
        issuer=overrides.get("issuer", ISSUER),
        audience=overrides.get("audience", AUDIENCE),
        authorized_party=overrides.get("authorized_party", CLIENT_ID),
        leeway_seconds=0,
    )


def test_successful_verification_with_pinned_key(material):
    primary, _, jwk, _ = material
    assert verify(make_token(primary), [jwk]).subject == "person-123"


def test_key_rotation_accepts_approved_new_key(material):
    _, rotated, old_jwk, rotated_jwk = material
    token = make_token(rotated, kid="key-2")
    with pytest.raises(IdentityRejected):
        verify(token, [old_jwk])
    assert verify(token, [old_jwk, rotated_jwk]).subject == "person-123"


@pytest.mark.parametrize("field,value", [
    ("kid", "unknown"),
    ("kid", ""),
    ("kid", "x" * 300),
])
def test_invalid_kid_rejected(material, field, value):
    primary, _, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary, headers={field: value}), [jwk])


def test_missing_kid_rejected(material):
    primary, _, jwk, _ = material
    token = jwt.encode(claims(), primary, algorithm="RS256")
    with pytest.raises(IdentityRejected):
        verify(token, [jwk])


def test_duplicate_kid_rejected(material):
    primary, _, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary), [jwk, dict(jwk)])


@pytest.mark.parametrize("change", [
    {"kty": "EC"},
    {"alg": "HS256"},
    {"use": "enc"},
    {"key_ops": ["sign"]},
    {"d": "secret"},
    {"n": "!malformed base64"},
])
def test_bad_jwk_rejected(material, change):
    primary, _, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary), [{**jwk, **change}])


@pytest.mark.parametrize("field", ["jku", "x5u", "jwk"])
def test_token_suggested_keys_and_urls_rejected(material, field):
    primary, _, jwk, _ = material
    suggested = {field: "https://attacker.test/keys.json"} if field != "jwk" else {field: jwk}
    with pytest.raises(IdentityRejected):
        verify(make_token(primary, headers=suggested), [jwk])


@pytest.mark.parametrize("updates", [
    {"iss": "https://evil.test"},
    {"aud": "unrelated-service"},
    {"aud": [AUDIENCE, "unrelated-service"]},
    {"azp": "unknown-client"},
    {"azp": None},
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=3)},
])
def test_wrong_claim_rejected(material, updates):
    primary, _, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary, payload=claims(**updates)), [jwk])


def test_different_signing_key_with_same_kid_rejected(material):
    primary, rotated, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(rotated, kid="key-1"), [jwk])


@pytest.mark.parametrize("keys", [[], [{"kid": "x"}] * 33, "not-a-list"])
def test_invalid_key_sets_rejected(material, keys):
    primary, _, _, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary), keys)


def test_oversized_token_rejected(material):
    primary, _, jwk, _ = material
    token = make_token(primary, payload=claims(extra="x" * 18000))
    with pytest.raises(IdentityRejected):
        verify(token, [jwk])


def test_authorized_party_is_required(material):
    primary, _, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary), [jwk], authorized_party="")


def test_non_rs256_token_rejected(material):
    _, _, jwk, _ = material
    forged = jwt.encode(claims(), "not-a-key" * 5, algorithm="HS256", headers={"kid": "key-1"})
    with pytest.raises(IdentityRejected):
        verify(forged, [jwk])


def test_non_access_jwt_type_rejected(material):
    private, _, jwk, _ = material
    token = make_token(private, headers={"typ": "id+jwt"})
    with pytest.raises(IdentityRejected):
        verify(token, [jwk])


def test_critical_header_not_supported(material):
    primary, _, jwk, _ = material
    token = make_token(primary, headers={"crit": ["example"], "example": "ignored"})
    with pytest.raises(IdentityRejected):
        verify(token, [jwk])


def test_http_issuer_configuration_not_permitted(material):
    primary, _, jwk, _ = material
    with pytest.raises(IdentityRejected):
        verify(make_token(primary), [jwk], issuer="http://login.example.test")
