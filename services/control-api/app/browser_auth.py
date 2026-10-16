"""Opt-in, localhost-only OIDC browser login via Authorization Code + PKCE.

The external OIDC identity provider authenticates the user. Metriqen never
accepts or stores account passwords and never takes issuer URLs from callers.
Redis holds *only* short-lived login flow state and opaque server sessions.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request as HTTPRequest, build_opener

import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jwt.exceptions import InvalidTokenError
import redis

from app.security.http_identity import PinnedIdentitySettings
from app.security.jwks import verify_access_token_from_jwks
from app.security.oidc import IdentityRejected, VerifiedIdentity
from app.storage.profiles import ProfileAlreadyExists, ProfileStore
from app.accounts import ProfileInput, canonical_name
from app.security.redis_quota import RedisAccountQuota

FLOW_COOKIE = "metriqen_oidc_flow"
SESSION_COOKIE = "metriqen_dev_session"
FLOW_SECONDS = 300
SESSION_SECONDS = 1800
TOKEN_LIMIT = 16384
LOCAL_ORIGIN = "http://127.0.0.1:8000"
CALLBACK = LOCAL_ORIGIN + "/auth/callback"
URLSAFE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
BROWSER_ENV = (
    "METRIQEN_BROWSER_AUTH_URL",
    "METRIQEN_BROWSER_TOKEN_URL",
    "METRIQEN_BROWSER_REDIS_URL",
)
OPTIONAL_SECRET = "METRIQEN_BROWSER_CLIENT_SECRET"


@dataclass(frozen=True, slots=True)
class BrowserAuthSettings:
    authorization_url: str
    token_url: str
    client_id: str
    client_secret: str | None
    identity: PinnedIdentitySettings

    def __post_init__(self):
        issuer = urlsplit(self.identity.issuer)
        for value in (self.authorization_url, self.token_url):
            address = urlsplit(value)
            if (address.scheme != "https" or address.netloc != issuer.netloc
                    or not address.path.startswith("/") or address.path == "/"
                    or address.username or address.password or address.query
                    or address.fragment or len(value) > 2048):
                raise ValueError("OIDC browser endpoints must be pinned HTTPS issuer-origin paths")
        if self.client_id != self.identity.authorized_party:
            raise ValueError("OIDC browser client id must equal pinned access-token azp")
        if self.client_secret is not None and (not 16 <= len(self.client_secret) <= 512
                                                or "\n" in self.client_secret):
            raise ValueError("OIDC browser client secret is invalid")


def browser_configured(env: Mapping[str, str] | None,
                       identity: PinnedIdentitySettings | None):
    variables = os.environ if env is None else env
    values = [variables.get(k, "") for k in BROWSER_ENV]
    secret = variables.get(OPTIONAL_SECRET, "")
    if not any(values) and not secret:
        return None
    if not all(values) or identity is None:
        raise ValueError("OIDC browser configuration is incomplete")
    settings = BrowserAuthSettings(
        authorization_url=values[0], token_url=values[1],
        client_id=identity.authorized_party,
        client_secret=secret or None,
        identity=identity,
    )
    url = urlsplit(values[2])
    if (url.scheme != "redis" or url.hostname != "127.0.0.1"
            or url.port != 6379 or url.path != "/0" or url.query or url.fragment
            or not url.username or not url.password or len(url.password) < 16
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", url.username)):
        raise ValueError("OIDC browser Redis requires authenticated loopback ACL service")
    connection = redis.Redis.from_url(values[2], socket_connect_timeout=1,
                                      socket_timeout=2, retry_on_timeout=False,
                                      decode_responses=False)
    return BrowserLogin(settings, BrowserSessionStore(connection), OIDCTokenExchange(settings))


class BrowserSessionStore:
    """Ephemeral Redis records, no access/refresh tokens and no profile data."""

    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def key(kind: str, ticket: str) -> str:
        if kind not in ("flow", "session") or not URLSAFE.fullmatch(ticket):
            raise ValueError("invalid opaque ticket")
        return "metriqen:browser:v1:" + kind + ":" + hashlib.sha256(ticket.encode()).hexdigest()

    def create(self, kind: str, ticket: str, value: dict, ttl: int) -> None:
        try:
            ok = self.connection.set(
                self.key(kind, ticket),
                json.dumps(value, separators=(",", ":")).encode(),
                ex=ttl, nx=True,
            )
            if ok is not True:
                raise ValueError("duplicate opaque ticket")
        except Exception:
            raise HTTPException(503, "browser session service unavailable") from None

    def consume_flow(self, ticket: str) -> dict | None:
        try:
            value = self.connection.getdel(self.key("flow", ticket))
            return json.loads(value) if value is not None else None
        except ValueError:
            return None
        except Exception:
            raise HTTPException(503, "browser session service unavailable") from None

    def get_session(self, ticket: str) -> dict | None:
        try:
            value = self.connection.get(self.key("session", ticket))
            return json.loads(value) if value is not None else None
        except ValueError:
            return None
        except Exception:
            raise HTTPException(503, "browser session service unavailable") from None

    def delete_session(self, ticket: str) -> None:
        try:
            self.connection.delete(self.key("session", ticket))
        except Exception:
            raise HTTPException(503, "browser session service unavailable") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OIDCTokenExchange:
    """Fixed HTTPS issuer-host token endpoint, bounded and redirect-free."""

    def __init__(self, settings: BrowserAuthSettings):
        self.settings = settings
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def exchange(self, code: str, verifier: str) -> dict:
        form = {
            "grant_type": "authorization_code",
            "code": code, "redirect_uri": CALLBACK,
            "client_id": self.settings.client_id,
            "code_verifier": verifier,
        }
        if self.settings.client_secret:
            form["client_secret"] = self.settings.client_secret
        body = urlencode(form).encode("ascii")
        req = HTTPRequest(self.settings.token_url, data=body, method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                   "Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=5) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("token endpoint content type")
                data = response.read(32769)
                if len(data) > 32768:
                    raise ValueError("token response too large")
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("token response is not object")
                return result
        except (OSError, URLError, HTTPError, ValueError, TypeError):
            raise HTTPException(502, "trusted OIDC token exchange failed") from None


class BrowserLogin:
    def __init__(self, settings: BrowserAuthSettings,
                 sessions: BrowserSessionStore,
                 exchanger: OIDCTokenExchange):
        self.settings = settings
        self.sessions = sessions
        self.exchanger = exchanger

    def validate_tokens(self, tokens: dict, nonce: str) -> VerifiedIdentity:
        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")
        if (not isinstance(id_token, str) or not isinstance(access_token, str)
                or len(id_token) > TOKEN_LIMIT or len(access_token) > TOKEN_LIMIT):
            raise HTTPException(401, "trusted OIDC login verification failed")
        try:
            header = jwt.get_unverified_header(id_token)
            kid = header.get("kid")
            if (header.get("alg") != "RS256" or not isinstance(kid, str)
                    or header.get("typ", "JWT").upper() not in ("JWT", "ID+JWT")
                    or {"jku", "x5u", "jwk", "crit"}.intersection(header)):
                raise ValueError("invalid id token headers")
            keys = self.settings.identity.trusted_jwks["keys"]
            matches = [k for k in keys if k["kid"] == kid]
            if len(matches) != 1:
                raise ValueError("untrusted id token kid")
            public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matches[0]))
            if public.key_size < 2048:
                raise ValueError("weak id token key")
            claims = jwt.decode(
                id_token, public, algorithms=["RS256"],
                audience=self.settings.client_id,
                issuer=self.settings.identity.issuer,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
                leeway=30,
            )
            if (not isinstance(claims.get("sub"), str) or not claims["sub"].strip()
                    or not hmac.compare_digest(str(claims.get("nonce", "")), nonce)
                    or isinstance(claims.get("aud"), list) and
                    (len(claims["aud"]) != 1 or claims["aud"][0] != self.settings.client_id)):
                raise ValueError("invalid subject, nonce or audience")
            token_header = jwt.get_unverified_header(access_token)
            if token_header.get("typ", "").upper() != "AT+JWT":
                raise ValueError("OIDC API access-token type invalid")
            verified = verify_access_token_from_jwks(
                access_token, trusted_jwks=self.settings.identity.trusted_jwks,
                issuer=self.settings.identity.issuer,
                audience=self.settings.identity.audience,
                authorized_party=self.settings.identity.authorized_party,
            )
            if verified.subject != claims["sub"]:
                raise ValueError("ID token subject differs from access-token subject")
            return verified
        except (IdentityRejected, InvalidTokenError, ValueError, TypeError,
                KeyError, AttributeError):
            raise HTTPException(401, "trusted OIDC login verification failed") from None


def browser_login_router(login: BrowserLogin, profiles: ProfileStore | None,
                         quota: RedisAccountQuota | None = None) -> APIRouter:
    router = APIRouter(tags=["browser-auth"], include_in_schema=False)

    def local(request: Request) -> None:
        if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
            raise HTTPException(403, "browser sign-in is local-only")

    def session(request: Request, *, required: bool = True) -> tuple[dict | None, str | None]:
        local(request)
        ticket = request.cookies.get(SESSION_COOKIE, "")
        if not URLSAFE.fullmatch(ticket):
            if required:
                raise HTTPException(401, "no active browser session")
            return None, None
        record = login.sessions.get_session(ticket)
        if (not isinstance(record, dict)
                or not all(isinstance(record.get(k), str) for k in ("issuer", "subject", "csrf"))
                or record["issuer"] != login.settings.identity.issuer):
            if required:
                raise HTTPException(401, "no active browser session")
            return None, None
        return record, ticket

    def same_origin_csrf(request: Request, record: dict) -> None:
        if (request.headers.get("origin") != LOCAL_ORIGIN
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or not hmac.compare_digest(request.headers.get("x-metriqen-csrf", ""),
                                           record["csrf"])):
            raise HTTPException(403, "invalid browser write origin or CSRF token")

    def identity(record: dict) -> VerifiedIdentity:
        return VerifiedIdentity(issuer=record["issuer"], subject=record["subject"],
                                audience=login.settings.identity.audience)

    @router.get("/auth/login")
    def start(request: Request) -> Response:
        local(request)
        ticket, browser_binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        login.sessions.create("flow", ticket, {
            "verifier": verifier, "nonce": nonce,
            "binding": hashlib.sha256(browser_binding.encode()).hexdigest(),
        }, FLOW_SECONDS)
        url = login.settings.authorization_url + "?" + urlencode({
            "response_type": "code",
            "client_id": login.settings.client_id,
            "redirect_uri": CALLBACK,
            "scope": "openid",
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": ticket,
            "nonce": nonce,
        })
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(FLOW_COOKIE, browser_binding, max_age=FLOW_SECONDS, path="/auth",
                            httponly=True, secure=False, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/auth/callback")
    def callback(request: Request, code: str = "", state: str = "",
                 error: str = "") -> Response:
        local(request)
        if error:
            raise HTTPException(401, "OIDC sign-in rejected")
        binding = request.cookies.get(FLOW_COOKIE, "")
        if (not URLSAFE.fullmatch(state) or not URLSAFE.fullmatch(binding)
                or not isinstance(code, str) or not 1 <= len(code) <= 2048
                or any(ord(c) <= 32 for c in code)):
            raise HTTPException(401, "OIDC sign-in state invalid")
        flow = login.sessions.consume_flow(state)
        if (not isinstance(flow, dict)
                or not all(isinstance(flow.get(k), str) for k in
                           ("binding", "verifier", "nonce"))
                or not hmac.compare_digest(flow["binding"], hashlib.sha256(binding.encode()).hexdigest())):
            raise HTTPException(401, "OIDC sign-in state invalid")
        tokens = login.exchanger.exchange(code, flow["verifier"])
        principal = login.validate_tokens(tokens, flow["nonce"])
        ticket = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        login.sessions.create("session", ticket, {
            "issuer": principal.issuer, "subject": principal.subject, "csrf": csrf,
        }, SESSION_SECONDS)
        response = RedirectResponse("/console/account", status_code=303)
        response.delete_cookie(FLOW_COOKIE, path="/auth")
        # Local development only. Internet-facing sessions REQUIRE HTTPS,
        # __Host- cookie prefix, secure=True and trusted TLS ingress.
        response.set_cookie(SESSION_COOKIE, ticket, max_age=SESSION_SECONDS, path="/",
                            httponly=True, secure=False, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/auth/session")
    def whoami(request: Request) -> dict:
        record, _ = session(request, required=False)
        if record is None:
            return {"authenticated": False}
        return {"authenticated": True, "subject": record["subject"],
                "issuer": record["issuer"], "csrf": record["csrf"]}

    @router.post("/auth/logout")
    def logout(request: Request) -> Response:
        record, ticket = session(request)
        same_origin_csrf(request, record)
        login.sessions.delete_session(ticket)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/auth/profile")
    def get_profile(request: Request) -> dict:
        record, _ = session(request)
        if profiles is None:
            raise HTTPException(503, "browser profile database unavailable")
        try:
            value = profiles.get(identity(record))
        except Exception:
            raise HTTPException(503, "browser profile database unavailable") from None
        if value is None:
            raise HTTPException(404, "profile not yet created")
        return value

    @router.post("/auth/profile", status_code=201)
    def create_profile(request: Request, payload: ProfileInput) -> dict:
        record, _ = session(request)
        same_origin_csrf(request, record)
        if profiles is None:
            raise HTTPException(503, "browser profile database unavailable")
        if quota is not None:
            quota.enforce(identity(record))
        try:
            return profiles.create(identity(record), canonical_name(payload.display_name))
        except ProfileAlreadyExists:
            raise HTTPException(409, "profile already exists") from None
        except Exception:
            raise HTTPException(503, "browser profile database unavailable") from None

    @router.patch("/auth/profile")
    def update_profile(request: Request, payload: ProfileInput) -> dict:
        record, _ = session(request)
        same_origin_csrf(request, record)
        if profiles is None:
            raise HTTPException(503, "browser profile database unavailable")
        if quota is not None:
            quota.enforce(identity(record))
        try:
            value = profiles.change_name(identity(record), canonical_name(payload.display_name))
        except Exception:
            raise HTTPException(503, "browser profile database unavailable") from None
        if value is None:
            raise HTTPException(404, "profile not yet created")
        return value

    assets = Path(__file__).resolve().parent / "account_console"
    csp = ("default-src 'none'; script-src 'self'; style-src 'self'; "
           "connect-src 'self'; base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'none'")
    def asset(filename: str, mime: str):
        def route() -> Response:
            return Response(
                content=(assets / filename).read_bytes(), media_type=mime,
                headers={"Content-Security-Policy": csp, "X-Frame-Options": "DENY",
                         "Cache-Control": "no-store"},
            )
        return route
    router.add_api_route("/console/account", asset("index.html", "text/html"), methods=["GET"])
    router.add_api_route("/console/account/app.js", asset("app.js", "application/javascript"), methods=["GET"])
    router.add_api_route("/console/account/app.css", asset("app.css", "text/css"), methods=["GET"])
    return router
