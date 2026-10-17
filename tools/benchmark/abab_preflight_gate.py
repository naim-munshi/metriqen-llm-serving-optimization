"""Offline A/B/A/B phase-to-preflight evidence consistency gate.

Revalidates all four already-recorded raw sweeps and quality reports through
the existing analyzer. Binds a separate, bounded read-only macOS preflight
snapshot to each phase's first recorded run timestamp, model digest, Ollama
version and operator-requested OLLAMA_NUM_PARALLEL.

This tool performs NO inference or system probes and CANNOT attest actual
runtime slots, GPU identity, model execution, or causal performance changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from tools.benchmark import abab_analysis, run_timing

PHASE_PARALLELISM = {"A1": 1, "B1": 2, "A2": 1, "B2": 2}
MAX_PREFLIGHT_BYTES = 65536
MAX_PREFLIGHT_AGE_SECONDS = 900
UTC_TIMESTAMP = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)\Z"
)
EXPECTED_REPORT_KEYS = frozenset((
    "schema_version", "kind", "timestamp_utc", "status", "expected",
    "observed", "blocking_reasons", "active_slots_verified", "limitations",
))


class PreflightEvidenceError(ValueError):
    """Controlled operator-facing error without private file content."""


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _no_nonfinite(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _local_json(path: Path, phase: str) -> dict:
    """Read a *small* local snapshot with no symlinks or duplicate JSON keys."""
    if path.suffix != ".json" or path.is_symlink():
        raise PreflightEvidenceError(f"{phase}: preflight must be an ordinary JSON file")
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_PREFLIGHT_BYTES:
            raise PreflightEvidenceError(f"{phase}: preflight missing or over 64 KiB")
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_no_nonfinite,
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, PreflightEvidenceError):
            raise
        raise PreflightEvidenceError(f"{phase}: invalid preflight JSON") from None
    if not isinstance(raw, dict) or set(raw) != EXPECTED_REPORT_KEYS:
        raise PreflightEvidenceError(f"{phase}: unsupported preflight report structure")
    return raw


def _utc(value: object, phase: str) -> datetime:
    if type(value) is not str or UTC_TIMESTAMP.fullmatch(value) is None:
        raise PreflightEvidenceError(f"{phase}: invalid UTC preflight timestamp")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PreflightEvidenceError(f"{phase}: invalid UTC preflight timestamp") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta():
        raise PreflightEvidenceError(f"{phase}: timestamp is not UTC")
    return timestamp.astimezone(timezone.utc)


def _check_preflight(phase: str, raw: dict, manifest: dict,
                     first_run: datetime, last_prior_run: datetime | None) -> dict:
    parallel = PHASE_PARALLELISM[phase]
    if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["kind"] != "local_mac_ollama_preflight"
            or raw["status"] != "preflight_checks_passed_slot_verification_required"
            or raw["blocking_reasons"] != []
            or raw["active_slots_verified"] is not False
            or not isinstance(raw["limitations"], list)
            or len(raw["limitations"]) == 0):
        raise PreflightEvidenceError(f"{phase}: preflight did not pass or pretends slots are attested")
    expected, observed = raw["expected"], raw["observed"]
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise PreflightEvidenceError(f"{phase}: expected/observed settings missing")
    digest = manifest.get("model_digest")
    model = manifest.get("model_tag")
    version = manifest.get("ollama_version")
    if (expected.get("model") != model or expected.get("model_digest") != digest
            or type(expected.get("configured_parallelism")) is not int
            or expected["configured_parallelism"] != parallel):
        raise PreflightEvidenceError(f"{phase}: preflight model/revision/parallelism differs from phase")
    observed_version = observed.get("ollama_version")
    if (type(observed_version) is not str
            or version not in (observed_version, "ollama-" + observed_version)):
        raise PreflightEvidenceError(f"{phase}: observed Ollama version differs from evidence")
    if (observed.get("host_system") != "Darwin"
            or observed.get("power_source") != "AC"
            or observed.get("gateway_live") is not True
            or type(observed.get("ollama_process_count")) is not int
            or observed["ollama_process_count"] != 1
            or type(observed.get("configured_parallelism")) is not int
            or observed["configured_parallelism"] != parallel
            or observed.get("installed_model_digest") != digest):
        raise PreflightEvidenceError(f"{phase}: observed power/process/model settings do not match")
    memory = observed.get("system_memory_free_percent")
    if memory is not None and (type(memory) is not int or not 0 <= memory <= 100):
        raise PreflightEvidenceError(f"{phase}: invalid memory observation")

    when = _utc(raw["timestamp_utc"], phase)
    age = (first_run - when).total_seconds()
    if not 0 <= age <= MAX_PREFLIGHT_AGE_SECONDS:
        raise PreflightEvidenceError(f"{phase}: preflight missing, stale or after first run")
    if last_prior_run is not None and when < last_prior_run:
        raise PreflightEvidenceError(f"{phase}: preflight predates completion of prior phase")
    return {
        "expected_parallelism": parallel,
        "preflight_to_first_run_seconds": round(age, 3),
        "configuration_consistent": True,
        "runtime_slots_attested": False,
    }


def validate(manifests: dict[str, Path], preflights: dict[str, Path]) -> dict:
    """Validate local reported config across four timed phase bundles.

    Raises if any synthetic or operator-supplied report is inconsistent.
    Success is evidence *consistency*, not verification of model execution.
    """
    if set(manifests) != set(PHASE_PARALLELISM) or set(preflights) != set(PHASE_PARALLELISM):
        raise PreflightEvidenceError("exactly A1/B1/A2/B2 manifests and preflights required")
    all_paths = [Path(path).resolve() for path in (*manifests.values(), *preflights.values())]
    if len(set(all_paths)) != 8:
        raise PreflightEvidenceError("four distinct manifests and four distinct preflights required")

    # Revalidate every benchmark and independent quality report through the
    # existing comparator; an isolated preflight alone proves nothing.
    analysis = abab_analysis.analyze(*(manifests[phase] for phase in PHASE_PARALLELISM))
    if analysis["chronology"]["classification"] != "timestamps_available_all_four":
        raise PreflightEvidenceError("timed raw sweeps are required for every A/B/A/B phase")
    previous_end = None
    results = {}
    for phase in PHASE_PARALLELISM:
        interval = analysis["chronology"]["phase_intervals"][phase]
        if not isinstance(interval, dict):
            raise PreflightEvidenceError(f"{phase}: missing first/last run time")
        first = run_timing.parse_utc(interval["start_utc"])
        finish = run_timing.parse_utc(interval["end_utc"])
        manifest = _local_json(manifests[phase], phase + " manifest")
        preflight = _local_json(preflights[phase], phase)
        results[phase] = _check_preflight(phase, preflight, manifest, first, previous_end)
        previous_end = finish
    return {
        "schema_version": 1,
        "classification": "operator_sourced_preflight_consistency_only",
        "timed_phase_order": "A1_B1_A2_B2",
        "phases": results,
        "limitations": [
            "Snapshots and timestamps are operator-supplied, not signed or independently attested",
            "Configured OLLAMA_NUM_PARALLEL does not verify active n_slots or n_seq_max",
            "The gateway, power and model observations do not prove true GPU runtime or execution",
            "Benchmark medians and reference string scores do not prove causal speedups or quality parity",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify four timed A/B/A/B benchmark phases against individual macOS preflights")
    for phase in PHASE_PARALLELISM:
        arg = phase.lower()
        parser.add_argument(f"--{arg}", type=Path, required=True)
        parser.add_argument(f"--{arg}-preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path,
                        help="Optional private JSON, created exclusively (never overwritten)")
    args = parser.parse_args(argv)
    manifests = {phase: getattr(args, phase.lower()) for phase in PHASE_PARALLELISM}
    preflights = {phase: getattr(args, phase.lower() + "_preflight")
                  for phase in PHASE_PARALLELISM}
    try:
        if args.output is not None and (
            args.output.suffix != ".json"
            or args.output.resolve() in {
                p.resolve() for p in (*manifests.values(), *preflights.values())
            }
        ):
            raise PreflightEvidenceError("output must be separate JSON")
        result = validate(manifests, preflights)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as file:
                json.dump(result, file, sort_keys=True, indent=2)
                file.write("\n")
        print("A/B/A/B phase preflight-to-evidence consistency verified for all four phases.")
        print("NOT VERIFIED: actual runtime slots, genuine Mac/GPU inference, or a causal speedup.")
        return 0
    except PreflightEvidenceError as exc:
        print(f"A/B/A/B preflight gate blocked: {exc}", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print("A/B/A/B preflight gate blocked: invalid or inconsistent local evidence",
              file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
