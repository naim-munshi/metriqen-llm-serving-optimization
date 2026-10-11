"""Signed-token verification tests using ephemeral keys, not external credentials."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.security.oidc import IdentityRejected, verify_access_token

ISSUER = "https://identity.example.test/realms/metriqen"
AUDIENCE = "metriqen-api"


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private, public_pem


def claims(**changes):
    now = datetime.now(timezone.utc)
    result = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "iat": now - timedelta(seconds=10),
        "nbf": now - timedelta(seconds=10),
        "exp": now + timedelta(minutes=5),
    }
    result.update(changes)
    return result


def signed(private, data=None, *, algorithm="RS256"):
    return jwt.encode(data if data is not None else claims(), private, algorithm=algorithm)


def verify(token, public):
    return verify_access_token(
        token,
        trusted_public_key_pem=public,
        issuer=ISSUER,
        audience=AUDIENCE,
        leeway_seconds=0,
    )


def test_valid_signed_token(keys):
    private, public = keys
    identity = verify(signed(private), public)
    assert identity.subject == "user-123"
    assert identity.issuer == ISSUER
    assert identity.audience == AUDIENCE


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.example.test"},
        {"aud": "other-api"},
        {"sub": ""},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=3)},
        {"nbf": datetime.now(timezone.utc) + timedelta(minutes=3)},
        {"iat": datetime.now(timezone.utc) + timedelta(minutes=3)},
        {"aud": [AUDIENCE, "other-api"]},
    ],
)
def test_bad_claims_rejected(keys, changes):
    private, public = keys
    with pytest.raises(IdentityRejected):
        verify(signed(private, claims(**changes)), public)


@pytest.mark.parametrize("field", ["iss", "aud", "sub", "exp", "iat", "nbf"])
def test_required_claim_missing(keys, field):
    private, public = keys
    data = claims()
    del data[field]
    with pytest.raises(IdentityRejected):
        verify(signed(private, data), public)


def test_wrong_signature_rejected(keys):
    _, public = keys
    outsider = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(IdentityRejected):
        verify(signed(outsider), public)


def test_unsigned_token_rejected(keys):
    _, public = keys
    token = jwt.encode(claims(), key="", algorithm="none")
    with pytest.raises(IdentityRejected):
        verify(token, public)


def test_malformed_token_rejected(keys):
    _, public = keys
    with pytest.raises(IdentityRejected):
        verify("this-is-not-a-jwt", public)


def test_invalid_configuration_rejected(keys):
    private, public = keys
    with pytest.raises(IdentityRejected):
        verify_access_token(signed(private), trusted_public_key_pem=public, issuer="", audience=AUDIENCE)


@pytest.mark.parametrize("leeway", [-1, 121])
def test_invalid_clock_leeway_rejected(keys, leeway):
    private, public = keys
    with pytest.raises(ValueError):
        verify_access_token(signed(private), trusted_public_key_pem=public, issuer=ISSUER, audience=AUDIENCE, leeway_seconds=leeway)
