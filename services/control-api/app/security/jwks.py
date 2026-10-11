"""Verify access tokens against an explicitly trusted in-memory public JWKS.

The JWKS must come from a trusted, issuer-pinned server-side source. This module
performs NO HTTP requests, and never resolves token-supplied URLs.
"""

import json
from collections.abc import Mapping
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from jwt.exceptions import InvalidTokenError

from app.security.oidc import IdentityRejected, VerifiedIdentity, verify_access_token

MAX_JWKS_KEYS = 32
MAX_TOKEN_BYTES = 16_384
FORBIDDEN_JWK_PRIVATE_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})
FORBIDDEN_JWT_HEADER_FIELDS = frozenset({"jwk", "jku", "x5u"})


def verify_access_token_from_jwks(
    token: str,
    *,
    trusted_jwks: Mapping[str, object],
    issuer: str,
    audience: str,
    authorized_party: str,
    leeway_seconds: int = 30,
) -> VerifiedIdentity:
    """Authenticate a token using one pinned signing key selected by kid.

    Reject missing/duplicate key IDs, private keys, wrong signing algorithms,
    unexpected calling clients, and cross-issuer/audience tokens. Key rotation
    is possible by replacing the trusted JWKS snapshot; secure key discovery
    and refresh are intentionally deferred to a separate milestone.
    """
    if not isinstance(token, str) or not token or not token.isascii() or len(token) > MAX_TOKEN_BYTES:
        raise IdentityRejected("invalid token size")
    issuer_url = urlsplit(issuer) if isinstance(issuer, str) else None
    if (
        issuer_url is None or issuer_url.scheme != "https" or not issuer_url.hostname
        or issuer_url.username or issuer_url.password or issuer_url.query or issuer_url.fragment
    ):
        raise IdentityRejected("issuer must be a trusted HTTPS URL")
    if not isinstance(authorized_party, str) or not authorized_party:
        raise IdentityRejected("authorized party must be configured")

    try:
        header = jwt.get_unverified_header(token)
    except (InvalidTokenError, TypeError, ValueError) as exc:
        raise IdentityRejected("access token rejected") from exc

    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not (1 <= len(kid) <= 256):
        raise IdentityRejected("unsupported token key reference")
    if FORBIDDEN_JWT_HEADER_FIELDS.intersection(header) or "crit" in header:
        raise IdentityRejected("token-provided signing keys are forbidden")

    if not isinstance(trusted_jwks, Mapping):
        raise IdentityRejected("invalid trusted key configuration")
    keys = trusted_jwks.get("keys")
    if not isinstance(keys, list) or not (1 <= len(keys) <= MAX_JWKS_KEYS):
        raise IdentityRejected("invalid trusted key set")
    if not all(isinstance(item, dict) for item in keys):
        raise IdentityRejected("invalid trusted key entry")

    matches = [item for item in keys if item.get("kid") == kid]
    if len(matches) != 1:
        raise IdentityRejected("unknown or ambiguous signing key")
    public_jwk = matches[0]
    if public_jwk.get("kty") != "RSA" or public_jwk.get("alg", "RS256") != "RS256":
        raise IdentityRejected("unsupported key type or algorithm")
    if public_jwk.get("use", "sig") != "sig":
        raise IdentityRejected("key not intended for signatures")
    if "key_ops" in public_jwk and public_jwk["key_ops"] != ["verify"]:
        raise IdentityRejected("key operations not restricted to verification")
    if FORBIDDEN_JWK_PRIVATE_FIELDS.intersection(public_jwk):
        raise IdentityRejected("private signing key must not be included in JWKS")

    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(public_jwk))
        if public_key.key_size < 2048:
            raise IdentityRejected("RSA signing key is too small")
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
    except (ValueError, TypeError, KeyError, OverflowError, InvalidTokenError) as exc:
        raise IdentityRejected("invalid RSA verification key") from exc

    return verify_access_token(
        token,
        trusted_public_key_pem=pem,
        issuer=issuer,
        audience=audience,
        expected_authorized_party=authorized_party,
        leeway_seconds=leeway_seconds,
    )
