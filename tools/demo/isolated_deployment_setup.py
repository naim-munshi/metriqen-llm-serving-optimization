"""Operator-only isolated local acceptance alias setup; no hosted Mac access.

Dry-run is GET-only. --execute creates only an absent, explicitly named
acceptance-* alias with a pinned registered/approved Ollama revision. It
never edits existing aliases, including assistant and rollback history.
--load-baseline is a separate explicit opt-in because model preload may
evict other models on memory-constrained Macs.
"""
from __future__ import annotations

import argparse
import sys

from tools.demo import local_deployment_acceptance as lifecycle

API = lifecycle.API
Failure = lifecycle.AcceptanceFailure


def _approved_digest(fetch, token: str, tag: str, role: str) -> tuple[str, bool]:
    digest = lifecycle._preflight_model_ready(
        fetch, token, tag, role=role, require_loaded=False
    )
    status = lifecycle._ok(fetch, "GET", API + "/model-statuses", token=token).get("models")
    if not isinstance(status, list):
        raise Failure("model_status_unavailable")
    matches = [m for m in status if isinstance(m, dict) and m.get("tag") == tag]
    if len(matches) != 1 or type(matches[0].get("loaded")) is not bool:
        raise Failure(role + "_model_status_invalid")
    return digest, matches[0]["loaded"]


def _installed_digest(fetch, token: str, tag: str, expected: str, role: str) -> None:
    found = lifecycle._ok(fetch, "GET", API + "/available-models", token=token).get("models")
    if not isinstance(found, list):
        raise Failure("installed_model_list_invalid")
    matches = [m for m in found if isinstance(m, dict) and m.get("tag") == tag]
    if len(matches) != 1:
        raise Failure(role + "_installed_model_missing_or_ambiguous")
    if matches[0].get("digest") != expected:
        raise Failure(role + "_installed_digest_mismatch")


def _existing_alias(fetch, token: str, name: str) -> dict | None:
    entries = lifecycle._ok(fetch, "GET", API + "/deployments", token=token).get("deployments")
    if not isinstance(entries, list):
        raise Failure("deployment_list_invalid")
    matches = [row for row in entries if isinstance(row, dict) and row.get("name") == name]
    if len(matches) > 1:
        raise Failure("isolated_alias_ambiguous")
    return matches[0] if matches else None


def _verify_alias(alias: dict | None, baseline: str, digest: str) -> None:
    if alias is None:
        return
    if alias.get("active_tag") != baseline or alias.get("active_digest") != digest:
        raise Failure("isolated_alias_revision_conflict")
    if alias.get("previous_tag") is not None or alias.get("previous_digest") is not None:
        raise Failure("isolated_alias_has_existing_rollback_history")
    if alias.get("enabled") is not True:
        raise Failure("isolated_alias_disabled_requires_manual_review")


def collect(*, token: str, name: str, baseline: str, candidate: str,
            execute: bool = False, load_baseline: bool = False,
            fetch=lifecycle.fetch_local) -> dict:
    # A dedicated test alias is mandatory; never accept the live assistant
    # name or unchecked client-supplied paths. Validate before any request.
    if (not isinstance(name, str) or lifecycle.NAME.fullmatch(name) is None
            or not name.startswith("acceptance-") or len(name) == len("acceptance-")
            or not isinstance(baseline, str) or lifecycle.TAG.fullmatch(baseline) is None
            or not isinstance(candidate, str) or lifecycle.TAG.fullmatch(candidate) is None
            or candidate == baseline):
        raise Failure("invalid_isolated_alias_or_model")
    if load_baseline and not execute:
        raise Failure("load_baseline_requires_execute")

    alias = _existing_alias(fetch, token, name)
    baseline_digest, baseline_loaded = _approved_digest(fetch, token, baseline, "baseline")
    candidate_digest, _ = _approved_digest(fetch, token, candidate, "candidate")
    _installed_digest(fetch, token, baseline, baseline_digest, "baseline")
    _installed_digest(fetch, token, candidate, candidate_digest, "candidate")
    _verify_alias(alias, baseline, baseline_digest)

    if not execute:
        return {
            "mode": "plan", "name": name,
            "isolated_alias_exists": alias is not None,
            "baseline_loaded": baseline_loaded,
            "candidate_approved_and_installed": True,
            "created": False,
            "executed_state_changes": False,
            "candidate_lifecycle_executed": False,
            "portfolio_p1_complete": False,
        }

    if not baseline_loaded:
        if not load_baseline:
            raise Failure("baseline_not_loaded_use_explicit_load_baseline")
        # Only the exact already-registered/approved/installed revision
        # can be loaded. This can evict other Ollama models.
        lifecycle._load_exact(fetch, token, baseline, baseline_digest)

    if alias is None:
        # SQLite enforces unique alias names and returns 409 on a
        # concurrent create. Never overwrite/adopt a competing record.
        status, response = fetch(
            "POST", API + "/deployments", token=token,
            payload={"name": name, "tag": baseline},
        )
        if status != 201:
            raise Failure("isolated_alias_create_status_unexpected")
        if (response.get("name") != name or
                response.get("active_tag") != baseline or
                response.get("active_digest") != baseline_digest or
                response.get("previous_tag") is not None or
                response.get("previous_digest") is not None or
                response.get("enabled") is not True):
            raise Failure("isolated_alias_create_response_invalid")

    # Check post-creation identity; no success claim from HTTP 201 alone.
    current = _existing_alias(fetch, token, name)
    if current is None:
        raise Failure("isolated_alias_missing_after_creation")
    _verify_alias(current, baseline, baseline_digest)
    if current.get("ready") is not True:
        raise Failure("isolated_alias_not_ready_after_setup")

    return {
        "mode": "execute", "name": name,
        "isolated_alias_exists": True,
        "baseline_loaded": True,
        "candidate_approved_and_installed": True,
        "created": alias is None,
        "executed_state_changes": alias is None or not baseline_loaded,
        "candidate_lifecycle_executed": False,
        "portfolio_p1_complete": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safely inspect/create a separate acceptance-* local alias; never change assistant"
    )
    parser.add_argument("--name", default="acceptance-test")
    parser.add_argument("--baseline", default="llama3.2:1b")
    parser.add_argument("--candidate", default="llama3.2:latest")
    parser.add_argument("--execute", action="store_true", help="Opt in to create an absent isolated alias")
    parser.add_argument("--load-baseline", action="store_true",
                        help="With --execute, permit loading the pinned baseline into Ollama memory")
    args = parser.parse_args(argv)
    try:
        token = lifecycle.operator_token(
            lifecycle.ROOT / "reports/local/.model-operator-token"
        )
        result = collect(
            token=token, name=args.name, baseline=args.baseline,
            candidate=args.candidate, execute=args.execute,
            load_baseline=args.load_baseline,
        )
    except Failure as exc:
        print("Isolated acceptance alias setup blocked: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError):
        print("Isolated acceptance alias setup blocked: invalid local configuration",
              file=sys.stderr)
        return 2

    if not args.execute:
        print("READ-ONLY PLAN: " + (
            "existing safe test alias" if result["isolated_alias_exists"]
            else "test alias missing; explicit --execute can create it"
        ))
        print("Baseline currently loaded: " + str(result["baseline_loaded"]).lower())
        print("Candidate registered, approved and installed at its pinned digest.")
        print("No models loaded, aliases created or existing deployment history changed.")
    else:
        print("ISOLATED ACCEPTANCE ALIAS: READY")
        print("Created new alias: " + str(result["created"]).lower())
        print("Alias: " + result["name"])
        print("No candidate promotion, inference or rollback executed.")
    print("Mac/browser and running gateway candidate policy still require operator acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
