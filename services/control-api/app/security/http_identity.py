"""Fail-closed HTTP access-token boundary with an operator-pinned public JWKS.

This deliberately supports one local identity-inspection route only. It does
not grant organization/project membership, API keys, or inference privileges.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request
from jwt.exceptions import InvalidTokenError

from app.security.jwks import MAX_JWKS_KEYS, MAX_TOKEN_BYTES, verify_access_token_from_jwks
from app.security.oidc import IdentityRejected, VerifiedIdentity

ENV_KEYS = (
    "METRIQEN_OIDC_ISSUER",
    "METRIQEN_OIDC_AUDIENCE",
    "METRIQEN_OIDC_AUTHORIZED_PARTY",
    "METRIQEN_OIDC_JWKS_PATH",
)
MAX_JWKS_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class PinnedIdentitySettings:
    issuer: str
    audience: str
    authorized_party: str
    trusted_jwks: Mapping[str, object]


def load_identity_settings(environ: Mapping[str, str] | None = None) -> PinnedIdentitySettings | None:
    """Load trusted *public* signing keys once at startup, never per request.

    The operator supplies a local JWKS file; requests and token headers cannot
    influence its path or trigger a network retrieval.
    """
    env = os.environ if environ is None else environ
    supplied = [env.get(key, "") for key in ENV_KEYS]
    if not any(supplied):
        return None  # Health-only installation; protected route returns 503.
    if not all(supplied) or any(not item.strip() for item in supplied):
        raise ValueError("OIDC configuration is incomplete")

    issuer, audience, authorized_party, filename = supplied
    try:
        parsed = urlsplit(issuer)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("OIDC issuer must be an explicit HTTPS issuer")
        path = Path(filename)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_JWKS_BYTES:
            raise ValueError("trusted JWKS file is absent or exceeds 64 KiB")
        jwks = json.loads(path.read_bytes())
        if not isinstance(jwks, dict) or set(jwks) != {"keys"}:
            raise ValueError("trusted JWKS must contain only public keys")
        keys = jwks["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_JWKS_KEYS:
            raise ValueError("trusted JWKS key count is invalid")
        key_ids: set[str] = set()
        forbidden = {"d", "p", "q", "dp", "dq", "qi", "oth"}
        for key in keys:
            if (not isinstance(key, dict) or key.get("kty") != "RSA"
                    or key.get("alg", "RS256") != "RS256"
                    or key.get("use", "sig") != "sig"
                    or forbidden.intersection(key)
                    or not isinstance(key.get("kid"), str)
                    or not 1 <= len(key["kid"]) <= 256
                    or not all(isinstance(key.get(name), str) and key[name] for name in ("n", "e"))
                    or key["kid"] in key_ids):
                raise ValueError("trusted JWKS has invalid or duplicate public keys")
            key_ids.add(key["kid"])
        return PinnedIdentitySettings(
            issuer=issuer, audience=audience, authorized_party=authorized_party,
            trusted_jwks=jwks,
        )
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("trusted OIDC configuration could not be loaded") from exc


def authenticated_identity(request: Request, settings: PinnedIdentitySettings | None) -> VerifiedIdentity:
    """Require one short, signed, dedicated access JWT; never trust JWT roles.

    Generic JWT/ID tokens are rejected by demanding typ=at+jwt at this HTTP
    boundary. This is intentionally stricter than the reusable JWKS library.
    """
    if settings is None:
        raise HTTPException(status_code=503, detail="identity verification is not configured")
    unauthorized = HTTPException(
        status_code=401,
        detail="invalid bearer access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    auth_headers = request.headers.getlist("authorization")
    if len(auth_headers) != 1:
        raise unauthorized
    authorization = auth_headers[0]
    if not isinstance(authorization, str) or len(authorization) > MAX_TOKEN_BYTES + 7:
        raise unauthorized
    scheme, sep, token = authorization.partition(" ")
    if (not sep or scheme.lower() != "bearer" or not token or token != token.strip()
            or " " in token or "\t" in token):
        raise unauthorized
    try:
        header = jwt.get_unverified_header(token)
        if not isinstance(header.get("typ"), str) or header["typ"].upper() != "AT+JWT":
            raise unauthorized
        return verify_access_token_from_jwks(
            token,
            trusted_jwks=settings.trusted_jwks,
            issuer=settings.issuer,
            audience=settings.audience,
            authorized_party=settings.authorized_party,
        )
    except (IdentityRejected, InvalidTokenError, TypeError, UnicodeError):
        raise unauthorized from None
