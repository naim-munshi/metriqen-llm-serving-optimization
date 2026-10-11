"""OIDC access-token signature and claim verification.

Caller must obtain public_key_pem from a trusted issuer configuration or a
validated JWKS resolver. Never accept a key supplied inside the token.
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
) -> VerifiedIdentity:
    """Verify signed RS256 access-token claims and return minimal identity.

    JWKS key discovery, trusted key rotation, session handling and database
    membership lookup are separate future components. This method does not
    authorize organization/project access.
    """
    if not token or not issuer or not audience or not trusted_public_key_pem:
        raise IdentityRejected("missing token verifier configuration")
    if not (0 <= leeway_seconds <= 120):
        raise ValueError("leeway_seconds must be between 0 and 120")

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise IdentityRejected("unsupported signing algorithm")
        if header.get("typ", "JWT").upper() not in {"JWT", "AT+JWT"}:
            raise IdentityRejected("invalid token type")
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
        if isinstance(claims["aud"], list):
            # No ambiguous multi-audience tokens until azp/client binding is defined.
            if claims["aud"] != [audience]:
                raise IdentityRejected("multiple audiences not supported")
        return VerifiedIdentity(subject=subject, issuer=issuer, audience=audience)
    except (InvalidTokenError, jwt.exceptions.DecodeError, KeyError, TypeError) as exc:
        raise IdentityRejected("access token rejected") from exc
