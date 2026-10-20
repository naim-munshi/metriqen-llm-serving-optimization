"""Guarded P3 FIFO admission experiment per-phase collector (real Mac only).

Read-only AC/digest/Ollama/gateway checks precede every inference request.
Caller configures exactly one Ollama server and a loopback Go gateway manually.
This tool neither changes processes nor authenticates runtime slots/thermals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from tools.benchmark import admission_load, phase_run, preflight
from tools.nlp_eval import online

ROOT = phase_run.ROOT
QUEUE_DEPTH_BY_PHASE = {"A": 0, "B": 2}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class AdmissionPhaseError(ValueError):
    """Fixed error codes without leaking private observations."""


def _check_inputs(*, phase: str, digest: str, parallelism: int,
                  hardware: str, directory: Path) -> None:
    if phase not in QUEUE_DEPTH_BY_PHASE:
        raise AdmissionPhaseError("invalid_phase")
    if DIGEST.fullmatch(digest) is None:
        raise AdmissionPhaseError("invalid_model_digest")
    if type(parallelism) is not int or parallelism not in (1, 2):
        raise AdmissionPhaseError("expected_ollama_parallelism_must_be_1_or_2")
    if (not isinstance(hardware, str) or not 4 <= len(hardware) <= 120
            or "\n" in hardware or "\r" in hardware):
        raise AdmissionPhaseError("hardware_label_required")
    base = phase_run.PRIVATE.resolve()
    path = directory.resolve()
    if (path == base or not path.is_relative_to(base) or
            directory.is_symlink() or directory.exists()):
        raise AdmissionPhaseError("new_private_phase_directory_required")
    for dataset in ("docs/benchmarks/workload.v1.jsonl",
                    "docs/nlp-eval/workload.v1.jsonl"):
        if not (ROOT / dataset).is_file():
            raise AdmissionPhaseError("versioned_dataset_missing")


def _report_is_present(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def collect(*, phase: str, digest: str, parallelism: int,
            hardware: str, directory: Path) -> None:
    _check_inputs(phase=phase, digest=digest, parallelism=parallelism,
                  hardware=hardware, directory=directory)
    # Never infer from a CLI label that active Ollama runtime slots match.
    # This is a read-only AC/version/digest/process-config preflight only.
    start = preflight.collect(expected_model_digest=digest,
                              expected_parallelism=parallelism)
    directory = phase_run._directory(directory, create=True)
    phase_run._private_json(directory / "preflight.json", start)
    if start.get("status") != "preflight_checks_passed_slot_verification_required":
        raise AdmissionPhaseError("preflight_blocked_private_report_saved")

    # Distinct from Ollama NUM_PARALLEL: Go FIFO queue depth is the sole
    # experimental variable allowed to differ between the A and B sweeps.
    depth = QUEUE_DEPTH_BY_PHASE[phase]
    admission_load.admission_snapshot(
        "http://127.0.0.1:8081/v1/chat/completions", depth,
    )
    observed = start.get("observed", {})
    version = observed.get("ollama_version")
    if not isinstance(version, str) or not version:
        raise AdmissionPhaseError("observed_ollama_version_missing")
    runtime = "ollama-" + version
    common = [
        "--model", "llama3.2:1b",
        "--model-revision", digest,
        "--runtime", runtime,
        "--hardware", hardware,
        "--max-tokens", "128",
        "--timeout", "120",
    ]
    overload_dir = directory / "overload"
    args = [
        *common,
        "--dataset", str(ROOT / "docs/benchmarks/workload.v1.jsonl"),
        "--expected-queue-depth", str(depth),
        "--repeats", "3", "--requests", "36", "--warmup", "2",
        "--output-dir", str(overload_dir),
    ]
    if admission_load.main(args) != 0:
        raise AdmissionPhaseError("overload_sweep_failed_partial_reports_retained")
    if not _report_is_present(overload_dir / "admission-summary.json"):
        raise AdmissionPhaseError("overload_summary_missing")

    quality = directory / "quality.json"
    quality_args = [
        *common,
        "--dataset", str(ROOT / "docs/nlp-eval/workload.v1.jsonl"),
        "--output", str(quality),
    ]
    if online.main(quality_args) != 0 or not _report_is_present(quality):
        raise AdmissionPhaseError("quality_failed_partial_reports_retained")

    # Confirm the same gateway admission policy remained in effect after
    # both overload and quality traffic. Snapshot requires an idle gateway.
    admission_load.admission_snapshot(
        "http://127.0.0.1:8081/v1/chat/completions", depth,
    )
    # This catches a power/config mismatch at the end, but does not
    # establish uninterrupted AC or independently verify active n_slots.
    end = preflight.collect(expected_model_digest=digest,
                            expected_parallelism=parallelism)
    phase_run._private_json(directory / "postflight.json", end)
    if end.get("status") != "preflight_checks_passed_slot_verification_required":
        raise AdmissionPhaseError("postflight_blocked_private_report_saved")
    for path in [directory / "preflight.json", directory / "postflight.json", quality,
                 *overload_dir.glob("*.json")]:
        if path.is_file() and not path.is_symlink():
            path.chmod(0o600)
    if overload_dir.is_dir():
        overload_dir.chmod(0o700)
    print("P3 admission phase collection: PASS (private reports present, no causal attestation)")
    print("Phase:", phase, "queue depth:", depth, "configured Ollama parallelism:", parallelism)
    print("Actual n_slots, uninterrupted AC, thermals and real execution remain operator assertions.")
    print("Manual reviewer must check P3 quality parity and all HTTP 429/goodput/latency tradeoffs.")
    print("Private phase directory:", directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P3 Mac operator-only FIFO phase preflight, overload sweep and quality check"
    )
    parser.add_argument("--phase", choices=("A", "B"), required=True)
    parser.add_argument("--expected-model-digest", required=True)
    parser.add_argument("--expected-parallelism", type=int, choices=(1, 2), required=True)
    parser.add_argument("--hardware", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        collect(phase=args.phase, digest=args.expected_model_digest,
                parallelism=args.expected_parallelism, hardware=args.hardware,
                directory=args.output_dir)
        return 0
    except (AdmissionPhaseError, OSError, ValueError, TypeError, KeyError) as exc:
        code = str(exc) if isinstance(exc, AdmissionPhaseError) else "invalid_local_phase_configuration"
        print("P3 operator phase blocked: " + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
