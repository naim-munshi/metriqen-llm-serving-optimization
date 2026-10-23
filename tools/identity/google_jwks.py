"""Operator-invoked Google *public* JWKS refresh with exact endpoint pinning.

Not used during HTTP request handling. No user/token-supplied URLs, no OAuth
credentials, no redirects, no ambient proxy, no non-TLS fallback.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
MAX_BYTES = 64 * 1024
MAX_KEYS = 32
RSA_B64 = re.compile(r"[A-Za-z0-9_-]+\Z")
ROOT = Path(__file__).resolve().parents[2]
PUBLIC_KEY_PATH = ROOT / "reports" / "local" / "google-public-jwks.json"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError("duplicate JSON field in Google key document")
        out[key] = value
    return out


def _integer(encoded: str, field: str) -> int:
    if (not isinstance(encoded, str) or not 2 <= len(encoded) <= 2048
            or RSA_B64.fullmatch(encoded) is None):
        raise ValueError(f"invalid Google public key {field}")
    try:
        bits = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, base64.binascii.Error):
        raise ValueError(f"invalid Google public key {field}") from None
    if not bits or bits[0] == 0:
        raise ValueError(f"invalid Google public key {field}")
    return int.from_bytes(bits, byteorder="big")


def normalize_public_jwks(data: bytes) -> bytes:
    """Validate and canonicalize only RSA public signing material."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ValueError("Google JWKS exceeds size limit or is empty")
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Google JWKS is not valid JSON") from None
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("invalid Google JWKS structure")
    source = document["keys"]
    if not 1 <= len(source) <= MAX_KEYS:
        raise ValueError("invalid Google JWKS key count")
    clean, kids = [], set()
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("invalid Google JWKS key entry")
        if item.get("kty") != "RSA" or item.get("alg", "RS256") != "RS256":
            raise ValueError("Google JWKS requires RSA RS256 signing keys")
        if item.get("use", "sig") != "sig" or (
                "key_ops" in item and item["key_ops"] != ["verify"]):
            raise ValueError("Google JWKS has non-signing public key")
        if any(k in item for k in ("d", "p", "q", "dp", "dq", "qi", "oth")):
            raise ValueError("Google JWKS contains private key material")
        kid = item.get("kid")
        if (not isinstance(kid, str) or not 1 <= len(kid) <= 256
                or not kid.isascii() or not kid.isprintable()
                or kid in kids):
            raise ValueError("Google JWKS has missing or duplicate key identifier")
        modulus = _integer(item.get("n"), "modulus")
        exponent = _integer(item.get("e"), "exponent")
        if not 2048 <= modulus.bit_length() <= 8192 or exponent != 65537:
            raise ValueError("Google public RSA parameters are unsupported")
        kids.add(kid)
        clean.append({
            "kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
            "n": item["n"], "e": item["e"],
        })
    return (json.dumps({"keys": clean}, sort_keys=True, separators=(",", ":"))
            + "\n").encode("utf-8")


def fetch_public_jwks(*, opener=None) -> bytes:
    """Read only the fixed Google HTTPS origin; operator invokes explicitly."""
    if opener is None:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(JWKS_URL, method="GET",
                      headers={"Accept": "application/json",
                               "User-Agent": "Metriqen-Google-Public-JWKS/1"})
    try:
        with opener.open(request, timeout=10) as response:
            if response.geturl() != JWKS_URL:
                raise ValueError("unexpected Google JWKS response origin")
            if response.headers.get_content_type() != "application/json":
                raise ValueError("Google JWKS response is not JSON")
            value = response.read(MAX_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ValueError("trusted Google public JWKS fetch failed") from None
    return normalize_public_jwks(value)


def _private_destination() -> Path:
    directory = PUBLIC_KEY_PATH.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (directory.is_symlink() or not directory.is_dir()
            or directory.stat().st_mode & 0o077):
        raise ValueError("private JWKS directory must be owner-only (0700)")
    dest = PUBLIC_KEY_PATH
    if dest.is_symlink():
        raise ValueError("existing JWKS destination must not be a symlink")
    if dest.exists():
        mode = dest.stat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("existing JWKS must be a private regular file (0600)")
    return dest


def write_public_jwks(payload: bytes, *, replace: bool = False) -> Path:
    """Write validated public keys privately, without partial writes."""
    clean = normalize_public_jwks(payload)
    dest = _private_destination()
    if dest.exists() and not replace:
        raise FileExistsError("existing public JWKS requires --replace")
    fd, temp_path = tempfile.mkstemp(prefix=".google-jwks-", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(clean)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and dest.exists():
            raise FileExistsError("JWKS created concurrently; refusing replacement")
        os.replace(temp_path, dest)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pin Google public JWKS on this Mac; never handles OAuth secrets")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--refresh", action="store_true",
                         help="explicitly fetch Google's fixed public-key endpoint")
    actions.add_argument("--check", action="store_true",
                         help="validate the current private public-key snapshot offline")
    parser.add_argument("--replace", action="store_true",
                        help="explicitly approve atomic replacement of existing public key snapshot")
    args = parser.parse_args(argv)
    if args.check and args.replace:
        parser.error("--replace is valid only with --refresh")
    try:
        path = _private_destination()
        if args.refresh:
            result = fetch_public_jwks()
            write_public_jwks(result, replace=args.replace)
        else:
            result = normalize_public_jwks(path.read_bytes())
        count = len(json.loads(result)["keys"])
        print(f"Google public JWKS validated: {count} keys; private file: {path}")
        print("Restart the opt-in local FastAPI OIDC server to load a rotated JWKS snapshot.")
        return 0
    except (OSError, ValueError) as exc:
        # Never echo HTTP bodies, token contents, or external service error text.
        print(f"Google public JWKS operation refused: {type(exc).__name__}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
