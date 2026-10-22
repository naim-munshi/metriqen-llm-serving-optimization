"""Canonical Host defense for opt-in localhost-only control-plane routes.

Binding to loopback and validating peer IP do not validate the HTTP Host:
a third-party DNS name may resolve to 127.0.0.1. Reject such names before
serving cookie-authenticated browser or operator API routes. No DNS lookup.
"""
from __future__ import annotations

import re

from starlette.requests import Request

LOCAL_HTTP_PEERS = frozenset(("127.0.0.1", "::1"))
LOCAL_PATH_PREFIXES = ("/auth", "/console/account", "/console/models",
                       "/api/v1/local")
CANONICAL_LOCAL_HOST = re.compile(
    r"(?:127\.0\.0\.1|localhost|\[::1\]):([1-9][0-9]{0,4})\Z"
)


def is_local_only_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/")
               for prefix in LOCAL_PATH_PREFIXES)


def valid_local_request_host(request: Request) -> bool:
    """Use the untrusted Host header, not URL parsing/DNS or X-Forwarded-Host."""
    if request.client is None or request.client.host not in LOCAL_HTTP_PEERS:
        return False
    values = request.headers.getlist("host")
    if len(values) != 1:
        return False
    match = CANONICAL_LOCAL_HOST.fullmatch(values[0])
    if match is None:
        return False
    return 1 <= int(match.group(1)) <= 65535
