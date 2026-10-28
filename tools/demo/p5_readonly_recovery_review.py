"""Offline P5 read-only Ollama outage/recovery health-transition evidence reviewer.

Consumes three *existing* owner-private reports from local_readonly_review.
No network, processes, models, browser controls or state changes. The
transition is necessary but INSUFFICIENT for P5 inference recovery acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import stat
import sys

from tools.demo import local_readonly_review as local

MAX_BYTES = 64 * 1024
EXPECTED = {name: required for name, _, _, required in local.PROBES}


class ReviewBlocked(ValueError):
    """Only fixed, non-sensitive error codes are returned to the operator."""


def _unique_pairs(pairs):
    doc = {}
    for key, value in pairs:
        if key in doc:
            raise ReviewBlocked("duplicate_json_key")
        doc[key] = value
    return doc


def _load(path: Path) -> dict:
    base = local.REPORT_ROOT.absolute()
    target = path.expanduser().absolute()
    if (".." in path.parts or target.parent != base or target.suffix != ".json"
            or target.is_symlink() or base.is_symlink() or base.parent.is_symlink()):
        raise ReviewBlocked("evidence_path_not_private_local_json")
    if not base.is_dir() or stat.S_IMODE(base.stat().st_mode) & 0o077:
        raise ReviewBlocked("private_directory_permissions_invalid")
    info = target.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
            or info.st_size == 0 or info.st_size > MAX_BYTES):
        raise ReviewBlocked("evidence_permissions_or_size_invalid")
    try:
        doc = json.loads(
            target.read_bytes().decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _x: (_ for _ in ()).throw(ReviewBlocked("nonfinite_json")),
        )
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ReviewBlocked):
            raise
        raise ReviewBlocked("evidence_json_invalid") from None
    if not isinstance(doc, dict):
        raise ReviewBlocked("evidence_root_not_object")
    return doc


def _timestamp(doc: dict) -> datetime:
    raw = doc.get("observed_at_utc")
    if not isinstance(raw, str):
        raise ReviewBlocked("observation_time_missing")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ReviewBlocked("observation_time_invalid") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ReviewBlocked("observation_time_has_no_timezone")
    return stamp


def _validated(doc: dict) -> tuple[datetime, dict]:
    if (doc.get("schema") != "metriqen.local-readonly-review.v1"
            or doc.get("scope") != "operator_requested_local_get_only"
            or doc.get("portfolio_p4_or_p5_approved") is not False
            or doc.get("real_model_inference_verified") is not False
            or doc.get("mac_network_listener_binding_verified") is not False
            or doc.get("browser_visual_acceptance_verified") is not False
            or doc.get("oauth_signin_verified") is not False):
        raise ReviewBlocked("unexpected_source_report_contract")
    checks = doc.get("checks")
    if not isinstance(checks, list) or len(checks) != len(EXPECTED):
        raise ReviewBlocked("source_checks_missing")
    indexed = {}
    for check in checks:
        if not isinstance(check, dict):
            raise ReviewBlocked("source_check_invalid")
        name = check.get("check")
        required = check.get("required")
        status = check.get("status")
        http = check.get("http_status")
        if (not isinstance(name, str) or name not in EXPECTED
                or name in indexed or type(required) is not bool
                or required != EXPECTED[name]
                or status not in ("pass", "fail", "optional_unavailable")
                or (required and status == "optional_unavailable")
                or (not required and status == "fail")
                or (http is not None and (type(http) is not int or not 100 <= http <= 599))):
            raise ReviewBlocked("source_check_invalid")
        indexed[name] = {"status": status, "http_status": http}
    return _timestamp(doc), indexed


def _pass(checks: dict, name: str, status: int) -> bool:
    return (checks[name]["status"] == "pass"
            and checks[name]["http_status"] == status)


def review(baseline: Path, outage: Path, recovered: Path) -> dict:
    paths = (baseline, outage, recovered)
    if len({p.expanduser().absolute() for p in paths}) != 3:
        raise ReviewBlocked("source_reports_must_be_distinct")
    times, states = zip(*(_validated(_load(path)) for path in paths))
    if not (times[0] < times[1] < times[2]):
        raise ReviewBlocked("observation_order_invalid")
    first, failure, last = states
    for state in (first, last):
        if not all(_pass(state, name, 401 if name == "model_manager_unauthenticated" else 200)
                   for name, required in EXPECTED.items() if required):
            raise ReviewBlocked("healthy_boundary_missing")
    # A real gateway 503 on its upstream API version probe, *while its own
    # liveness remains 200*, is the narrow observed health transition.
    # The direct Ollama version probe must also fail (without using
    # any model inference or credentials). An HTTP 503 alone is not a
    # verified crash cause; it might be a transport or protocol error.
    if (not _pass(failure, "gateway_live", 200)
            or not _pass(failure, "gateway_console", 200)
            or failure["gateway_upstream"] != {"status": "fail", "http_status": 503}
            or failure["ollama_version"]["status"] != "fail"):
        raise ReviewBlocked("unavailable_upstream_transition_missing")
    return {
        "schema": "metriqen.p5-readonly-health-transition.v1",
        "classification": "operator_sourced_http_health_transition_only",
        "observed_readonly_health_transition": True,
        "baseline_time_reported_utc": times[0].isoformat(timespec="seconds"),
        "outage_time_reported_utc": times[1].isoformat(timespec="seconds"),
        "recovered_time_reported_utc": times[2].isoformat(timespec="seconds"),
        "checks_passed": [
            "healthy_gateway_and_upstream_before",
            "gateway_live_while_upstream_unreachable_503",
            "direct_ollama_version_unavailable_during_outage",
            "healthy_gateway_and_upstream_after",
            "ordered_private_source_reports",
        ],
        "gateway_process_continuity_verified": False,
        "failed_inference_response_verified": False,
        "successful_inference_after_recovery_verified": False,
        "real_browser_recovery_verified": False,
        "p5_reliability_security_gate_complete": False,
        "limitations": [
            "Offline consistency of operator-created GET-only snapshots; no independent authenticity",
            "No process identity, crash reason, gateway restart, or model/GPU attestation",
            "No sanitized inference failure, admission release, cancellation or successful recovery chat proven",
            "No OIDC, Redis, PostgreSQL, tenant RBAC or public exposure security signoff",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline, fail-closed P5 GET-only health-transition review")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--outage", type=Path)
    parser.add_argument("--recovered", type=Path)
    parser.add_argument("--output", type=Path, help="Optional fresh 0600 JSON under reports/local")
    args = parser.parse_args(argv)
    paths = (args.baseline, args.outage, args.recovered)
    if all(path is None for path in paths) and args.output is None:
        print("PLAN ONLY: supply three existing local_readonly_review JSON reports.")
        print("Offline compare only; never stops Ollama, sends inference, or approves P5.")
        return 0
    if any(path is None for path in paths):
        print("P5 offline review blocked: three_source_reports_required", file=sys.stderr)
        return 2
    try:
        result = review(*paths)
        if args.output is not None:
            if args.output.expanduser().absolute() in {p.expanduser().absolute() for p in paths}:
                raise ReviewBlocked("output_overlaps_source")
            local.save_private(result, args.output)
    except ReviewBlocked as exc:
        print("P5 offline review blocked: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print("P5 offline review blocked: invalid_local_report_or_path", file=sys.stderr)
        return 2
    print("P5 read-only HTTP health transition: PASS")
    print("Inference failure/recovery, gateway process continuity, and security: NOT VERIFIED")
    print("P5 portfolio acceptance: NOT YET COMPLETE")
    if args.output is not None:
        print("Sanitized review stored under private reports/local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
