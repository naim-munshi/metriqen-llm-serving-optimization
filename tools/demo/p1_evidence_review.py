"""Conservative offline review of operator-supplied P1 CLI evidence.

This is a consistency checker for two privately stored JSON observations;
it is NOT hardware attestation, browser evidence, or P1 approval. It never
contacts localhost services or copies model-generated content.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import sys

from tools.demo.real_ollama import ROOT, private_report

HEX = re.compile(r"[0-9a-f]{64}\Z")
SMOKE_CHECKS = frozenset((
    "installed_model_digest_before_and_after",
    "ollama_native_nonstream_chat",
    "gateway_nonstream_chat",
    "gateway_streaming_delta_and_done",
    "matching_loaded_digest_after",
    "gateway_two_completed_attempt_metrics",
))
LIFECYCLE_CHECKS = frozenset((
    "deployment_active_revision_and_readiness",
    "original_approved_model_loaded",
    "stopped_alias_rejects_chat_409",
    "started_alias_routes_real_inference",
    "promoted_alias_routes_candidate_inference",
    "rollback_restores_original_inference",
    "final_original_alias_enabled",
))
MAX_BYTES = 65536


class EvidenceError(ValueError):
    """Fixed non-sensitive validation codes only."""


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_field")
        result[key] = value
    return result


def _read(path: Path) -> dict:
    base = (ROOT / "reports" / "local").resolve()
    if path.suffix != ".json" or path.is_symlink() or not path.resolve().is_relative_to(base):
        raise EvidenceError("evidence_must_be_private_local_json")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or
            info.st_size == 0 or info.st_mode & 0o077):
        raise EvidenceError("evidence_file_permissions_or_size_invalid")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("nonfinite_json")))
    except (ValueError, UnicodeError) as error:
        if isinstance(error, EvidenceError):
            raise
        raise EvidenceError("invalid_evidence_json") from None
    if not isinstance(data, dict):
        raise EvidenceError("evidence_root_must_be_object")
    return data


def _time(value: object) -> str:
    if not isinstance(value, str):
        raise EvidenceError("timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError("timestamp_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceError("timestamp_timezone_required")
    return parsed.isoformat(timespec="seconds")


def _checks(value: object, required: frozenset[str]) -> list[str]:
    if (not isinstance(value, list) or any(not isinstance(x, str) for x in value) or
            len(value) != len(set(value)) or not required.issubset(value)):
        raise EvidenceError("missing_or_duplicate_acceptance_check")
    if len(value) > 32:
        raise EvidenceError("too_many_check_names")
    return sorted(required)


def review(smoke_path: Path, lifecycle_path: Path) -> dict:
    if smoke_path.resolve() == lifecycle_path.resolve():
        raise EvidenceError("reports_must_be_distinct")
    smoke, life = _read(smoke_path), _read(lifecycle_path)
    if (smoke.get("schema_version") != 1 or
            smoke.get("kind") != "operator_local_real_model_smoke_observation" or
            smoke.get("passed") is not True or
            smoke.get("model_inference_http_observed") is not True or
            smoke.get("performance_benchmark") is not False or
            smoke.get("actual_gpu_execution_verified") is not False or
            smoke.get("gateway") != "127.0.0.1:8081"):
        raise EvidenceError("smoke_observation_not_eligible")
    model = smoke.get("model_tag")
    digest = smoke.get("model_digest")
    if (not isinstance(model, str) or not isinstance(digest, str) or
            not HEX.fullmatch(digest)):
        raise EvidenceError("smoke_model_identity_invalid")
    smoke_checks = _checks(smoke.get("tests_passed"), SMOKE_CHECKS)

    if (life.get("schema_version") != 1 or
            life.get("kind") != "operator_local_deployment_lifecycle_observation" or
            life.get("passed") is not True or
            life.get("executed_state_changes") is not True or
            life.get("actual_model_execution_http_observed") is not True or
            life.get("hardware_acceleration_verified") is not False or
            life.get("performance_benchmark") is not False or
            life.get("portfolio_p1_complete") is not False):
        raise EvidenceError("lifecycle_observation_not_eligible")
    if (life.get("original_tag") != model or
            life.get("original_digest") != digest):
        raise EvidenceError("smoke_lifecycle_digest_or_tag_mismatch")
    candidate = life.get("candidate_tag")
    candidate_digest = life.get("candidate_digest")
    if (not isinstance(candidate, str) or not candidate or candidate == model or
            not isinstance(candidate_digest, str) or not HEX.fullmatch(candidate_digest)):
        raise EvidenceError("candidate_revision_missing")
    if not isinstance(life.get("deployment"), str) or not life["deployment"]:
        raise EvidenceError("deployment_name_missing")
    lifecycle_checks = _checks(life.get("checks_passed"), LIFECYCLE_CHECKS)
    sequential = life.get("sequential_model_load")
    if type(sequential) is not bool:
        raise EvidenceError("lifecycle_load_mode_missing")
    if sequential:
        _checks(life.get("checks_passed"), frozenset((
            "candidate_loaded_at_promotion",
            "baseline_reloaded_before_rollback",
        )))
    smoke_time = _time(smoke.get("timestamp_utc"))
    lifecycle_time = _time(life.get("timestamp_utc"))
    return {
        "schema_version": 1,
        "classification": "operator_sourced_p1_cli_evidence_consistent_browser_review_pending",
        "p1_cli_consistency_checks_passed": True,
        "portfolio_p1_complete": False,
        "model_tag": model,
        "model_digest_prefix": digest[:12],
        "candidate_tag": candidate,
        "candidate_digest_prefix": candidate_digest[:12],
        "deployment": life["deployment"],
        "sequential_model_load": sequential,
        "smoke_timestamp_utc_reported": smoke_time,
        "lifecycle_timestamp_utc_reported": lifecycle_time,
        "smoke_checks_reviewed": smoke_checks,
        "lifecycle_checks_reviewed": lifecycle_checks,
        "operator_review_required": [
            "Human review of source CLI logs and genuine local runtime settings",
            "Visual browser approval, routing, promotion and rollback screenshot review",
            "Explicit approval before publishing sanitized public evidence",
        ],
        "not_verified": [
            "Cryptographic or GPU/Metal execution attestation",
            "TTFT, p95 latency, requests per second, model quality or optimization",
            "OIDC, project RBAC or production tenant isolation",
            "Independent authenticity of operator-created JSON",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review two private P1 reports without revealing secrets")
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--output", type=Path,
                        help="Optional new owner-only report under reports/local/")
    args = parser.parse_args(argv)
    try:
        result = review(args.smoke, args.lifecycle)
        if args.output is not None:
            if args.output.resolve() in (args.smoke.resolve(), args.lifecycle.resolve()):
                raise EvidenceError("output_overlaps_source")
            private_report(args.output, result)
        print("P1 private CLI evidence consistency: PASS")
        print("Real browser evidence / independent operator review: PENDING")
        print("P1 portfolio acceptance: NOT YET COMPLETE")
        if args.output is not None:
            print("Sanitized review created under reports/local; inspect before sharing.")
        return 0
    except EvidenceError as exc:
        print("P1 evidence review blocked: " + str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError):
        print("P1 evidence review blocked: invalid local report or configuration", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
