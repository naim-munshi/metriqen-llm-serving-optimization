"""OIDC access-token signature and claim verification.

The signing key MUST originate from trusted server configuration or a pinned
issuer key resolver. A token may not provide its own trusted key or URL.
"""

from dataclasses import dataclass
from datetime import timedelta

import jwt
from jwt.exceptions import InvalidTokenError


class IdentityRejected(ValueError):
    """An untrusted token cannot establish an authenticated identity."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    subject: str
    issuer: str
    audience: str


def verify_access_token(
    token: str,
    *,
    trusted_public_key_pem: str,
    issuer: str,
    audience: str,
    leeway_seconds: int = 30,
    expected_authorized_party: str | None = None,
) -> VerifiedIdentity:
    """Verify a signed RS256 access token against trusted issuer settings.

    If provided, expected_authorized_party enforces the OIDC azp client ID.
    Login, live JWKS discovery, session and membership lookup are not included.
    """
    if not token or not issuer or not audience or not trusted_public_key_pem:
        raise IdentityRejected("missing token verifier configuration")
    if expected_authorized_party is not None and not expected_authorized_party:
        raise IdentityRejected("authorized party configuration missing")
    if not (0 <= leeway_seconds <= 120):
        raise ValueError("leeway_seconds must be between 0 and 120")

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise IdentityRejected("unsupported signing algorithm")
        if header.get("typ", "JWT").upper() not in {"JWT", "AT+JWT"}:
            raise IdentityRejected("invalid token type")
        if {"jku", "x5u", "jwk"}.intersection(header):
            raise IdentityRejected("token-supplied signing keys are forbidden")
        claims = jwt.decode(
            token,
            key=trusted_public_key_pem,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            leeway=timedelta(seconds=leeway_seconds),
            options={
                "require": ["sub", "iss", "aud", "exp", "iat", "nbf"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject.strip():
            raise IdentityRejected("invalid subject")
        if isinstance(claims["aud"], list) and claims["aud"] != [audience]:
            raise IdentityRejected("multiple audiences not supported")
        if expected_authorized_party is not None and claims.get("azp") != expected_authorized_party:
            raise IdentityRejected("unauthorized calling client")
        return VerifiedIdentity(subject=subject, issuer=issuer, audience=audience)
    except (InvalidTokenError, KeyError, TypeError, AttributeError) as exc:
        raise IdentityRejected("access token rejected") from exc
