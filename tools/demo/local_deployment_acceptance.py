"""Operator-invoked real local deployment lifecycle acceptance (no cloud or downloads).

Defaults to read-only preflight. --execute explicitly authorizes local
Stop -> denied Chat -> Start -> Chat -> Promote -> Chat -> Rollback -> Chat.
The script attempts guarded restoration on error; it never claims atomicity
across HTTP requests or runtime/GPU performance attestation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tools.demo.real_ollama import private_report

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:8000"
API = "/api/v1/local"
NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}:[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ACTION = frozenset(("stop", "start", "promote", "rollback", "chat"))
PROMPT = "Reply with exactly: METRIQEN_DEPLOYMENT_READY"
MAX_BYTES = 65536


class AcceptanceFailure(ValueError):
    """Return only fixed codes, never upstream bodies or private credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), NoRedirect())


def endpoint(method: str, path: str) -> str:
    """Allowlist exact loopback endpoint families, disallow URL injection."""
    if method == "GET" and path in (
        API + "/models", API + "/model-statuses", API + "/deployments",
    ):
        return ORIGIN + path
    parts = path.split("/")
    if (method == "POST" and len(parts) == 7 and
            parts[:5] == ["", "api", "v1", "local", "deployments"] and
            NAME.fullmatch(parts[5]) and parts[6] in SAFE_ACTION):
        return ORIGIN + path
    raise AcceptanceFailure("endpoint_not_allowlisted")


def fetch_local(method: str, path: str, *, token: str, payload: dict | None = None) -> tuple[int, dict]:
    url = endpoint(method, path)
    if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii() or any(
        ord(c) < 33 or ord(c) > 126 for c in token
    ):
        raise AcceptanceFailure("invalid_operator_token")
    if (method == "GET" and payload is not None) or (method == "POST" and payload is None):
        raise AcceptanceFailure("invalid_request_payload")
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, method=method, headers=headers, data=data)
    try:
        try:
            response = _OPENER.open(request, timeout=125 if path.endswith("/chat") else 8)
        except HTTPError as exc:
            if exc.code != 409:
                exc.close()
                raise AcceptanceFailure("local_api_http_" + str(exc.code)) from None
            response = exc
        with response:
            code = response.status
            if response.headers.get_content_type() != "application/json":
                raise AcceptanceFailure("unexpected_response_type")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise AcceptanceFailure("response_too_large")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise AcceptanceFailure("response_not_object")
        return code, body
    except (URLError, TimeoutError, OSError):
        raise AcceptanceFailure("local_api_unreachable") from None
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, AcceptanceFailure):
            raise
        raise AcceptanceFailure("invalid_json_response") from None


def operator_token(path: Path) -> str:
    """Read only a local regular private token file; never echo its contents."""
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(
        (ROOT / "reports" / "local").resolve()
    ):
        raise AcceptanceFailure("token_file_must_be_private_reports_local")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise AcceptanceFailure("token_file_permissions_too_open")
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise AcceptanceFailure("token_file_unreadable") from None


def _ok(fetch, method: str, path: str, *, token: str, payload: dict | None = None) -> dict:
    code, value = fetch(method, path, token=token, payload=payload)
    if code != 200:
        raise AcceptanceFailure("unexpected_local_api_status_" + str(code))
    return value


def _deployment(fetch, token: str, name: str) -> dict:
    body = _ok(fetch, "GET", API + "/deployments", token=token)
    items = body.get("deployments")
    if not isinstance(items, list):
        raise AcceptanceFailure("deployment_list_invalid")
    found = [v for v in items if isinstance(v, dict) and v.get("name") == name]
    if len(found) != 1:
        raise AcceptanceFailure("deployment_not_found_or_ambiguous")
    return found[0]


def _model_ready(fetch, token: str, tag: str) -> str:
    models = _ok(fetch, "GET", API + "/models", token=token).get("models")
    states = _ok(fetch, "GET", API + "/model-statuses", token=token).get("models")
    if not isinstance(models, list) or not isinstance(states, list):
        raise AcceptanceFailure("model_status_unavailable")
    entries = [v for v in models if isinstance(v, dict) and v.get("tag") == tag]
    readiness = [v for v in states if isinstance(v, dict) and v.get("tag") == tag]
    if len(entries) != 1 or len(readiness) != 1:
        raise AcceptanceFailure("approved_model_missing")
    model, loaded = entries[0], readiness[0]
    digest = model.get("digest")
    if (not model.get("approved") or not isinstance(digest, str) or
            DIGEST.fullmatch(digest) is None or
            loaded.get("approved") is not True or
            loaded.get("loaded") is not True or
            loaded.get("digest_matches_loaded") is not True):
        raise AcceptanceFailure("approved_model_not_loaded")
    return digest


def _chat(fetch, token: str, name: str, tag: str) -> None:
    result = _ok(fetch, "POST", API + f"/deployments/{name}/chat",
                 token=token, payload={"prompt": PROMPT, "max_tokens": 48})
    if (result.get("deployment") != name or result.get("tag") != tag
            or not isinstance(result.get("content"), str) or
            not result["content"].strip()):
        raise AcceptanceFailure("deployment_inference_invalid")


def _check(fetch, token: str, name: str, *, tag: str, digest: str,
           enabled: bool, ready: bool | None = None, previous_tag: str | None = None,
           previous_digest: str | None = None) -> dict:
    d = _deployment(fetch, token, name)
    if (d.get("active_tag") != tag or d.get("active_digest") != digest or
            d.get("enabled") is not enabled):
        raise AcceptanceFailure("deployment_revision_or_state_changed")
    if ready is not None and d.get("ready") is not ready:
        raise AcceptanceFailure("deployment_readiness_unexpected")
    if previous_tag is not None and (
        d.get("previous_tag") != previous_tag or
        d.get("previous_digest") != previous_digest
    ):
        raise AcceptanceFailure("deployment_previous_revision_changed")
    return d


def _post(fetch, token: str, name: str, action: str, *, tag: str | None = None) -> dict:
    payload = {"tag": tag} if action == "promote" else {}
    return _ok(fetch, "POST", API + f"/deployments/{name}/{action}",
               token=token, payload=payload)


def collect(*, token: str, name: str, baseline: str, candidate: str | None,
            execute: bool, fetch=fetch_local) -> dict:
    if (not isinstance(name, str) or NAME.fullmatch(name) is None or
            not isinstance(baseline, str) or TAG.fullmatch(baseline) is None or
            candidate is not None and
            (not isinstance(candidate, str) or TAG.fullmatch(candidate) is None or candidate == baseline)):
        raise AcceptanceFailure("invalid_deployment_or_model_identifier")

    original = _deployment(fetch, token, name)
    original_digest = _model_ready(fetch, token, baseline)
    _check(fetch, token, name, tag=baseline, digest=original_digest,
           enabled=True, ready=True)
    # Never overwrite an existing rollback target without operator review.
    if original.get("previous_tag") is not None and candidate is not None:
        raise AcceptanceFailure("existing_rollback_history_requires_manual_review")
    candidate_digest = (_model_ready(fetch, token, candidate)
                        if candidate is not None else None)
    checks = ["deployment_active_revision_and_readiness",
              "original_approved_model_loaded"]
    if candidate is not None:
        checks.append("candidate_approved_model_loaded")

    if not execute:
        return {
            "schema_version": 1, "kind": "local_deployment_preflight",
            "passed": True, "executed_state_changes": False,
            "deployment": name, "original_tag": baseline,
            "candidate_tag": candidate, "checks_passed": checks,
            "portfolio_p1_complete": False,
        }

    # If a request fails mid-flight, restore only when the alias still points
    # at the exact known revision. No silent overwrite of concurrent edits.
    stopped, promoted = False, False
    try:
        _post(fetch, token, name, "stop")
        stopped = True
        _check(fetch, token, name, tag=baseline, digest=original_digest, enabled=False)
        code, _ = fetch("POST", API + f"/deployments/{name}/chat",
                        token=token, payload={"prompt": PROMPT, "max_tokens": 48})
        if code != 409:
            raise AcceptanceFailure("stopped_deployment_allowed_inference")
        checks.append("stopped_alias_rejects_chat_409")
        _post(fetch, token, name, "start")
        stopped = False
        _check(fetch, token, name, tag=baseline, digest=original_digest,
               enabled=True, ready=True)
        _chat(fetch, token, name, baseline)
        checks.append("started_alias_routes_real_inference")

        if candidate is not None:
            _post(fetch, token, name, "promote", tag=candidate)
            promoted = True
            _check(fetch, token, name, tag=candidate, digest=candidate_digest,
                   enabled=True, ready=True,
                   previous_tag=baseline, previous_digest=original_digest)
            _chat(fetch, token, name, candidate)
            checks.append("promoted_alias_routes_candidate_inference")
            _post(fetch, token, name, "rollback")
            promoted = False
            _check(fetch, token, name, tag=baseline, digest=original_digest,
                   enabled=True, ready=True,
                   previous_tag=candidate, previous_digest=candidate_digest)
            _chat(fetch, token, name, baseline)
            checks.append("rollback_restores_original_inference")

        _check(fetch, token, name, tag=baseline, digest=original_digest,
               enabled=True, ready=True)
        checks.append("final_original_alias_enabled")
        return {
            "schema_version": 1, "kind": "operator_local_deployment_lifecycle_observation",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "passed": True, "executed_state_changes": True,
            "deployment": name, "original_tag": baseline,
            "original_digest": original_digest, "candidate_tag": candidate,
            "candidate_digest": candidate_digest,
            "checks_passed": checks, "portfolio_p1_complete": False,
            "actual_model_execution_http_observed": True,
            "hardware_acceleration_verified": False,
            "performance_benchmark": False,
            "limitations": [
                "Real-local HTTP responses, not browser screenshots, GPU or cryptographic model attestation",
                "No throughput, TTFT, p95 or optimization measurements",
                "Local operator alias is not an OIDC project-scoped deployment",
            ],
        }
    except Exception:
        # Best-effort restoration; a concurrent operator may legitimately
        # change the deployment. Do not hide a failed acceptance or clobber.
        try:
            current = _deployment(fetch, token, name)
            if (promoted and current.get("active_tag") == candidate
                    and current.get("active_digest") == candidate_digest
                    and current.get("previous_tag") == baseline
                    and current.get("previous_digest") == original_digest):
                _post(fetch, token, name, "rollback")
            elif stopped and current.get("active_tag") == baseline and (
                    current.get("active_digest") == original_digest and
                    current.get("enabled") is False):
                _post(fetch, token, name, "start")
        except Exception:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in localhost deployment lifecycle P1 acceptance")
    parser.add_argument("--name", default="assistant")
    parser.add_argument("--baseline", default="llama3.2:1b")
    parser.add_argument("--candidate", help="Other already approved and loaded model; required for Promote/Rollback")
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize Stop/Start and optional Promote/Rollback")
    parser.add_argument("--token-file", type=Path,
                        default=ROOT / "reports/local/.model-operator-token")
    parser.add_argument("--output", type=Path, help="New private JSON report under reports/local/")
    args = parser.parse_args(argv)
    try:
        token = operator_token(args.token_file)
        result = collect(token=token, name=args.name, baseline=args.baseline,
                         candidate=args.candidate, execute=args.execute)
        if args.output:
            private_report(args.output, result)
        print("P1 local deployment " + ("lifecycle" if args.execute else "preflight") + ": PASS")
        print("Checks: " + ", ".join(result["checks_passed"]))
        print("Result is local operator evidence only; browser, GPU and benchmark gates remain separate.")
        if args.output:
            print("Private report saved under reports/local.")
        return 0
    except AcceptanceFailure as exc:
        print("P1 local deployment acceptance blocked: " + str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print("P1 local deployment acceptance blocked: invalid local configuration", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
