"""Operator-invoked, read-only localhost P4/P5 preflight.

Never performs model generation, account sign-in, token reading, state changes,
external network access, or a P1-P6 acceptance/release signoff. The default CLI
mode is an offline plan and touches neither local services nor files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "local"
MAX_RESPONSE_BYTES = 32 * 1024

# Exact, static loopback routes only. "account_ui" is an optional web
# page check, not proof of Google OAuth readiness or a session.
PROBES = (
    ("ollama_version", "http://127.0.0.1:11434/api/version", "ollama_json", True),
    ("gateway_live", "http://127.0.0.1:8081/health/live", "gateway_json", True),
    ("gateway_upstream", "http://127.0.0.1:8081/health/upstream", "upstream_json", True),
    ("gateway_console", "http://127.0.0.1:8081/console", "html", True),
    ("control_live", "http://127.0.0.1:8000/health/live", "control_json", True),
    ("model_manager_console", "http://127.0.0.1:8000/console/models", "html", True),
    ("model_manager_unauthenticated", "http://127.0.0.1:8000/api/v1/local/models", "must_401", True),
    ("account_console_page", "http://127.0.0.1:8000/console/account", "html", False),
)
_ALLOWED = frozenset(url for _, url, _, _ in PROBES)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_local(url: str) -> tuple[int, str, bytes]:
    """No redirects, inherited proxies, request bodies, auth headers or secrets."""
    if url not in _ALLOWED:
        raise ValueError("endpoint_not_allowlisted")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    req = Request(url, method="GET", headers={
        "Accept": "application/json, text/html",
        "User-Agent": "Metriqen-Read-Only-Local-Review/1",
    })
    try:
        with opener.open(req, timeout=4) as response:
            if response.geturl() != url:
                raise ValueError("redirected_local_response")
            code = response.status
            media = response.headers.get_content_type()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ValueError("oversized_local_response")
            return code, media, data
    except HTTPError as response:
        # Do NOT open or read any error body: it could contain user data,
        # exception details or redirects to a real identity provider.
        try:
            return response.code, "", b""
        finally:
            response.close()
    except (URLError, OSError, TimeoutError):
        raise ValueError("unreachable_local_service") from None


def _validated(code: int, media: str, data: bytes, kind: str) -> bool:
    if kind == "must_401":
        return code == 401
    if code != 200:
        return False
    if kind == "html":
        try:
            lower = data.decode("utf-8").lower()
        except UnicodeError:
            return False
        return (media == "text/html" and "<html" in lower
                and "metriqen" in lower and "</html>" in lower)
    if media != "application/json":
        return False
    try:
        payload = json.loads(data)
    except (UnicodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if kind == "ollama_json":
        return isinstance(payload.get("version"), str) and bool(payload["version"].strip())
    if kind == "gateway_json":
        return payload.get("service") == "metriqen-inference-gateway" and payload.get("status") == "ok"
    if kind == "control_json":
        return payload.get("service") == "metriqen-control-api" and payload.get("status") == "ok"
    if kind == "upstream_json":
        return payload.get("status") == "reachable" and payload.get("probe") == "ollama_api_version"
    return False


def collect(*, fetch=fetch_local) -> dict:
    """Never retain untrusted bytes, headers, cookies, model or identity data."""
    entries = []
    for name, url, kind, required in PROBES:
        try:
            code, media, body = fetch(url)
            # Reject bool (bool is subclass of int) or hostile fake values.
            if type(code) is not int or not 100 <= code <= 599 or not isinstance(media, str) or not isinstance(body, bytes):
                raise ValueError("invalid_local_transport_result")
            passed = _validated(code, media, body, kind)
            result = "pass" if passed else ("optional_unavailable" if not required else "fail")
        except (OSError, ValueError, TypeError):
            code = None
            result = "optional_unavailable" if not required else "fail"
        entries.append({
            "check": name,
            "status": result,
            "http_status": code,
            "required": required,
        })
    return {
        "schema": "metriqen.local-readonly-review.v1",
        "scope": "operator_requested_local_get_only",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": entries,
        "required_checks_passed": all(e["status"] == "pass" for e in entries if e["required"]),
        "oauth_signin_verified": False,
        "real_model_inference_verified": False,
        "browser_visual_acceptance_verified": False,
        "mac_network_listener_binding_verified": False,
        "portfolio_p4_or_p5_approved": False,
        "notes": [
            "The optional account web page does not mean Google OAuth is configured",
            "HTTP 200 on loopback does not prove every process is bound only to loopback",
            "No inference, browser rendering, keyboard navigation or recovery was exercised",
            "Check displayed source SHA against the actually running Go/FastAPI processes separately",
        ],
    }


def _private_output(path: Path) -> Path:
    """No symlinks, outside paths, existing files, or world-readable reports."""
    if ".." in path.parts:
        raise ValueError("unsafe_report_path")
    target = path.expanduser().absolute()
    base = REPORT_ROOT.absolute()
    if (target == base or target.suffix != ".json"
            or target.parent != base or base.is_symlink()
            or base.parent.is_symlink()
            or not target.is_relative_to(base)
            or target.is_symlink() or target.exists()):
        raise ValueError("unsafe_report_path")
    if not base.exists() or not base.is_dir() or stat.S_IMODE(base.stat().st_mode) & 0o077:
        raise ValueError("reports_local_requires_private_0700")
    return target


def save_private(report: dict, path: Path) -> Path:
    target = _private_output(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(report, out, indent=2, sort_keys=True)
            out.write("\n")
        os.chmod(target, 0o600)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only local UI/service preflight; does not approve P4/P5"
    )
    parser.add_argument("--execute", action="store_true",
                        help="issue exactly eight fixed localhost GET requests")
    parser.add_argument("--output", type=Path,
                        help="optional fresh private JSON path in reports/local/")
    args = parser.parse_args(argv)
    if not args.execute:
        if args.output is not None:
            parser.error("--output requires --execute")
        print("PLAN ONLY: eight fixed localhost GET probes; no network, model, credentials or files.")
        print("Use --execute for a strictly read-only, non-benchmark snapshot.")
        return 0
    report = collect()
    if args.output is not None:
        try:
            save_private(report, args.output)
        except (ValueError, OSError):
            print("Report was not saved: private path or permissions rejected.", file=sys.stderr)
            return 2
    for item in report["checks"]:
        print(f"{item['check']}: {item['status']} (HTTP {item['http_status']})")
    print("RESULT: " + ("LOCAL READ-ONLY PREFLIGHT OK" if report["required_checks_passed"]
                        else "LOCAL PREFLIGHT INCOMPLETE"))
    print("P1/P4/P5 acceptance NOT approved; Mac browser/identity/real inference unverified.")
    return 0 if report["required_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
