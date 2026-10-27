"""Operator-invoked single-phase P2 evidence collection with strict preflight.

Collect runs a real local 9-run sweep and 36-sample quality evaluation.
Finalize requires independent operator review and explicit attestation;
neither step restarts services, configures Ollama, or asserts GPU slots.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from tools.benchmark import preflight, sweep, evidence_bundle, evidence_gate
from tools.benchmark.power_monitor import PowerMonitor
from tools.nlp_eval import online

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports" / "local"
PHASES = {"A1": 1, "B1": 2, "A2": 1, "B2": 2}
SHA = re.compile(r"[0-9a-f]{64}\Z")


class PhaseError(ValueError):
    """Controlled diagnostic without private output or shell environments."""


def _directory(path: Path, *, create: bool) -> Path:
    base = PRIVATE.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise PhaseError("phase_directory_must_be_under_reports_local")
    if path.is_symlink():
        raise PhaseError("symlink_phase_directory_disallowed")
    if create:
        if path.exists():
            raise PhaseError("phase_directory_exists_no_overwrite")
        path.mkdir(parents=True, mode=0o700, exist_ok=False)
    elif not path.is_dir():
        raise PhaseError("phase_directory_missing")
    return path


def _private_json(path: Path, data: dict) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out, indent=2, sort_keys=True)
        out.write("\n")


def _read_summary(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size < 65536:
        raise PhaseError("missing_or_invalid_sweep_summary")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise PhaseError("sweep_summary_not_valid_json") from None
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise PhaseError("sweep_summary_schema_invalid")
    return result


def collect(*, phase: str, digest: str, hardware: str, directory: Path) -> None:
    if phase not in PHASES or not isinstance(digest, str) or not SHA.fullmatch(digest):
        raise PhaseError("invalid_phase_or_model_digest")
    if not isinstance(hardware, str) or not 4 <= len(hardware) <= 120 or "\n" in hardware:
        raise PhaseError("hardware_label_required")
    if not (ROOT / "docs/benchmarks/workload.v1.jsonl").is_file() or not (
            ROOT / "docs/nlp-eval/workload.v1.jsonl").is_file():
        raise PhaseError("versioned_datasets_missing")

    # Check before creating any output or requesting any model inference.
    snapshot = preflight.collect(expected_model_digest=digest,
                                 expected_parallelism=PHASES[phase])
    directory = _directory(directory, create=True)
    _private_json(directory / "preflight.json", snapshot)
    if snapshot["status"] != "preflight_checks_passed_slot_verification_required":
        raise PhaseError("preflight_blocked_check_private_preflight_json")
    version = snapshot["observed"]["ollama_version"]
    if not isinstance(version, str) or not version:
        raise PhaseError("observed_ollama_version_missing")
    runtime = "ollama-" + version
    benchdir = directory / "sweep"
    common = [
        "--model", "llama3.2:1b", "--model-revision", digest,
        "--runtime", runtime, "--hardware", hardware,
        "--max-tokens", "128", "--timeout", "120",
    ]
    # Reuse the same strict modules and fixed v1 workload; do not
    # summarize a partially failed set as a complete phase.
    bench_args = [
        *common, "--dataset", str(ROOT / "docs/benchmarks/workload.v1.jsonl"),
        "--repeats", "3", "--requests", "36", "--warmup", "2",
        "--output-dir", str(benchdir),
    ]
    with PowerMonitor(directory / "power-monitor.json") as monitor:
        if monitor.initial_source != "AC":
            raise PhaseError("phase_power_not_ac_before_inference")
        if sweep.main(bench_args) != 0:
            raise PhaseError("sweep_failed_partial_reports_retained")
        quality_args = [
            *common, "--dataset", str(ROOT / "docs/nlp-eval/workload.v1.jsonl"),
            "--output", str(benchdir / "quality.json"),
        ]
        if online.main(quality_args) != 0:
            raise PhaseError("quality_failed_partial_reports_retained")
    if monitor.status != "ac_samples_only":
        raise PhaseError("phase_power_changed_or_unknown_preserve_private_reports")
    # Owner-only directory already protects contents; restrict regular
    # report files additionally for accidental copying/archiving.
    for file in benchdir.iterdir():
        if file.is_file() and not file.is_symlink():
            file.chmod(0o600)
    print("P2 phase collection: PASS (timed sweep and quality files present)")
    print("Phase:", phase, "configured parallelism:", PHASES[phase])
    print("Raw observations are PRIVATE and operator-sourced; verify active slots manually.")
    print("Finalize only after reviewing source logs/quantization/power notes:", directory)


def finalize(*, phase: str, directory: Path, quantization: str,
             host_os: str, notes: str, confirm_real: bool) -> None:
    if phase not in PHASES:
        raise PhaseError("invalid_phase")
    if not confirm_real:
        raise PhaseError("explicit_real_model_observation_required")
    if any(not isinstance(item, str) or not 4 <= len(item) <= 400 for item in (
            quantization, host_os, notes)):
        raise PhaseError("quantization_host_os_and_notes_required")
    directory = _directory(directory, create=False)
    snapshot_path = directory / "preflight.json"
    if snapshot_path.is_symlink() or not snapshot_path.is_file():
        raise PhaseError("phase_preflight_missing")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PhaseError("phase_preflight_invalid") from None
    if (snapshot.get("status") != "preflight_checks_passed_slot_verification_required"
            or snapshot.get("expected", {}).get("configured_parallelism") != PHASES[phase]):
        raise PhaseError("preflight_not_eligible_for_phase")
    monitor_path = directory / "power-monitor.json"
    # Legacy phases may lack sampled power evidence. Never infer that they
    # maintained AC; those phases still require independent human review.
    if monitor_path.exists() or monitor_path.is_symlink():
        if (monitor_path.is_symlink() or not monitor_path.is_file()
                or not 0 < monitor_path.stat().st_size <= 1024 * 1024):
            raise PhaseError("phase_power_monitor_invalid")
        try:
            power_report = json.loads(monitor_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise PhaseError("phase_power_monitor_invalid") from None
        if (not isinstance(power_report, dict)
                or power_report.get("schema_version") != 1
                or power_report.get("status") != "ac_samples_only"):
            raise PhaseError("phase_power_monitor_not_stable")
    summary = _read_summary(directory / "sweep/sweep-summary.json")
    env = summary.get("environment")
    if (not isinstance(env, dict) or
            env.get("model") != "llama3.2:1b" or
            env.get("model_revision") != snapshot.get("expected", {}).get("model_digest") or
            env.get("runtime_self_reported") != "ollama-" + snapshot.get("observed", {}).get("ollama_version", "") or
            not isinstance(env.get("hardware_self_reported"), str)):
        raise PhaseError("sweep_and_phase_preflight_do_not_match")
    args = [
        "--sweep-dir", str(directory / "sweep"),
        "--quality-report", "quality.json",
        "--host-os", host_os, "--quantization", quantization,
        "--timestamp-utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--notes", notes, "--confirm-real-model-executed",
    ]
    if evidence_bundle.main(args) != 0:
        raise PhaseError("evidence_bundle_failed")
    manifest = directory / "sweep/evidence-manifest.json"
    manifest.chmod(0o600)
    if evidence_gate.main([str(manifest), "--verify-reports"]) != 0:
        raise PhaseError("strict_evidence_gate_failed")
    print("P2 phase finalization: PASS (local evidence consistency only)")
    print("Human-observed runtime slots and power/thermal notes remain operator assertions.")
    print("Four-phase A/B/A/B binding has NOT run; do not claim P2 finished.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local operator-only P2 phase collection and review")
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("collect", help="AC/pinned preflight then real 9-run sweep and 36 quality samples")
    run.add_argument("--phase", choices=tuple(PHASES), required=True)
    run.add_argument("--expected-model-digest", required=True)
    run.add_argument("--hardware", required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    end = subs.add_parser("finalize", help="Human-observed phase metadata then validate raw manifest")
    end.add_argument("--phase", choices=tuple(PHASES), required=True)
    end.add_argument("--output-dir", type=Path, required=True)
    end.add_argument("--quantization", required=True)
    end.add_argument("--host-os", required=True)
    end.add_argument("--notes", required=True)
    end.add_argument("--confirm-real-model-executed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            collect(phase=args.phase, digest=args.expected_model_digest,
                    hardware=args.hardware, directory=args.output_dir)
        else:
            finalize(phase=args.phase, directory=args.output_dir,
                     quantization=args.quantization, host_os=args.host_os,
                     notes=args.notes, confirm_real=args.confirm_real_model_executed)
        return 0
    except (PhaseError, OSError, ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, PhaseError) else "invalid_local_phase_configuration"
        print("P2 operator phase blocked: " + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
