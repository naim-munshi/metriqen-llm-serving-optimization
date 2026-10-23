"""AC-powered, operator-invoked *descriptive* Go SSE buffer A/B/A/B.

One phase per explicit invocation; NEVER starts/stops a gateway, edits an
environment, provisions resources, reads credentials or auto-claims speedup.
Dry-run by default. Live mode only targets the existing loopback gateway.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys

from tools.benchmark import gateway_overhead, runner

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "reports" / "local"
DATASET = ROOT / "docs" / "benchmarks" / "workload.v1.jsonl"
ENDPOINT = "http://127.0.0.1:8081/v1/chat/completions"
PHASE_BUFFER = {"A1": 1024, "B1": 4096, "A2": 1024, "B2": 4096}
LEVELS = (1, 2, 4)
_ENV = re.compile(r"(?:^|\s)METRIQEN_SSE_RELAY_BUFFER_BYTES=([^\s]+)")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class PhaseRefused(ValueError):
    """Use fixed error labels; do not print host environment or model output."""


def _capture(argv: list[str]) -> str:
    try:
        p = subprocess.run(argv, check=False, capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        raise PhaseRefused("mac_process_inspection_unavailable") from None
    if p.returncode:
        raise PhaseRefused("mac_process_inspection_failed")
    # Never log this command output: ps eww can contain credentials.
    return p.stdout


def inspect_gateway() -> dict:
    """Observe one active Go gateway listener and only its explicit SSE setting."""
    raw = _capture(["lsof", "-nP", "-t", "-iTCP:8081", "-sTCP:LISTEN"])
    values = raw.split()
    if len(values) != 1 or not values[0].isdigit():
        raise PhaseRefused("expected_exactly_one_gateway_listener")
    pid = int(values[0])
    if pid < 2:
        raise PhaseRefused("invalid_gateway_pid")
    process = _capture(["ps", "eww", "-p", str(pid)])
    found = _ENV.findall(process)
    if len(found) > 1 or (found and found[0] != "4096"):
        raise PhaseRefused("unsupported_running_sse_setting")
    return {
        "pid": pid,
        "buffer_bytes": 4096 if found else 1024,
        "evidence": "process_env_4096" if found else "env_unset_assumed_default",
        # Absence is not verifiable proof that this binary uses the
        # current source's default. Operator must inspect binary SHA.
        "binary_source_sha_verified": False,
    }


def verify_environment(model: str, digest: str, phase: str,
                       *, gateway_check=inspect_gateway,
                       runtime_check=gateway_overhead.preflight) -> dict:
    if phase not in PHASE_BUFFER:
        raise PhaseRefused("invalid_phase")
    if not _SHA.fullmatch(digest):
        raise PhaseRefused("invalid_expected_digest")
    if platform.system() != "Darwin":
        raise PhaseRefused("macos_required")
    try:
        runtime_check(model, digest)  # AC, installed digest, gateway upstream
        gateway = gateway_check()
    except (ValueError, OSError):
        raise PhaseRefused("real_runtime_or_ac_preflight_failed") from None
    if gateway["buffer_bytes"] != PHASE_BUFFER[phase]:
        raise PhaseRefused("gateway_buffer_does_not_match_phase")
    return gateway


def output_location(output: Path) -> Path:
    if ".." in output.parts:
        raise PhaseRefused("unsafe_output_path")
    target = output.expanduser().absolute()
    base = PRIVATE.absolute()
    if (target.parent != base or target.suffix != ".json"
            or base.is_symlink() or base.parent.is_symlink()
            or target.is_symlink() or target.exists()):
        raise PhaseRefused("output_must_be_fresh_private_local_json")
    if (not base.is_dir() or
            stat.S_IMODE(base.stat().st_mode) & 0o077):
        raise PhaseRefused("reports_local_must_be_private_0700")
    return target


def _save_once(destination: Path, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(destination, 0o600)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def collect(phase: str, model: str, digest: str, output: Path,
            *, requests: int = 12, tokens: int = 64,
            inspect=inspect_gateway, runtime=gateway_overhead.preflight,
            measure=runner.run) -> dict:
    if phase not in PHASE_BUFFER:
        raise PhaseRefused("invalid_phase")
    if type(requests) is not int or not 8 <= requests <= 36:
        raise PhaseRefused("requests_must_be_8_to_36")
    if type(tokens) is not int or not 1 <= tokens <= 128:
        raise PhaseRefused("tokens_must_be_1_to_128")
    dest = output_location(output)
    dataset, fingerprint = runner.read_dataset(DATASET)
    before = verify_environment(model, digest, phase,
                                gateway_check=inspect, runtime_check=runtime)
    results = []
    for concurrency in LEVELS:
        # Before each level, refuse changed AC, digest, upstream or process.
        current = verify_environment(model, digest, phase,
                                     gateway_check=inspect, runtime_check=runtime)
        if current != before:
            raise PhaseRefused("gateway_process_changed_during_phase")
        observation = measure(
            ENDPOINT, model, dataset, stream=True, max_tokens=tokens,
            concurrency=concurrency, requests=requests, warmup=1,
            timeout=120.0,
        )
        if (type(observation.get("successes")) is not int or
                type(observation.get("failures")) is not int or
                observation["successes"] + observation["failures"] != requests):
            raise PhaseRefused("invalid_runner_request_accounting")
        results.append({
            "concurrency": concurrency,
            "requests": requests,
            "successes": observation["successes"],
            "failures": observation["failures"],
            "requests_per_second": observation["requests_per_second"],
            "p95_latency_ms_success_only": observation["latency_ms"]["p95"],
            "p95_ttft_ms_success_only": observation["ttft_ms"]["p95"],
            "completion_token_coverage": observation["completion_token_coverage"],
        })
        print(f"{phase} C={concurrency}: {observation['successes']}/{requests} succeeded",
              flush=True)
    after = verify_environment(model, digest, phase,
                               gateway_check=inspect, runtime_check=runtime)
    if after != before:
        raise PhaseRefused("gateway_process_changed_during_phase")
    document = {
        "schema": "metriqen.sse-buffer-phase.v1",
        "phase": phase,
        "buffer_bytes": PHASE_BUFFER[phase],
        "observed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_tag": model,
        "model_digest": digest,
        "dataset_sha256": fingerprint,
        "stream": True,
        "max_tokens": tokens,
        "warmup_per_concurrency": 1,
        "gateway_process": before,
        "results": results,
        "real_macos_execution_reported_by_operator": True,
        "actual_ollama_slots_or_thermals_attested": False,
        "matched_answer_quality_verified": False,
        "causal_optimization_verified": False,
        "p3_gate_approved": False,
        "scope": "descriptive_go_gateway_phase_not_model_speedup",
    }
    _save_once(dest, document)
    return document


def _load_phase(path: Path, expected: str) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 32768:
        raise PhaseRefused("phase_missing_oversized_or_symlinked")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise PhaseRefused("phase_json_invalid") from None
    if (not isinstance(data, dict) or data.get("schema") != "metriqen.sse-buffer-phase.v1"
            or data.get("phase") != expected
            or data.get("buffer_bytes") != PHASE_BUFFER[expected]
            or data.get("stream") is not True
            or data.get("p3_gate_approved") is not False
            or data.get("causal_optimization_verified") is not False):
        raise PhaseRefused("phase_contract_invalid")
    vals = data.get("results")
    if (not isinstance(vals, list) or len(vals) != 3 or
            [x.get("concurrency") for x in vals if isinstance(x, dict)] != list(LEVELS)):
        raise PhaseRefused("phase_result_shape_invalid")
    for v in vals:
        count, success, failure = (v.get(k) for k in ("requests", "successes", "failures"))
        if (any(type(x) is not int or x < 0 for x in (count, success, failure))
                or count < 8 or count > 36 or success + failure != count):
            raise PhaseRefused("phase_request_accounting_invalid")
        for k in ("requests_per_second", "p95_latency_ms_success_only",
                  "p95_ttft_ms_success_only"):
            n = v.get(k)
            if n is not None and (type(n) not in (int, float) or
                                  not 0 <= n < 10**9):
                raise PhaseRefused("phase_nonfinite_metric")
    return data


def review(paths: dict[str, Path]) -> dict:
    if set(paths) != set(PHASE_BUFFER):
        raise PhaseRefused("four_phases_required")
    data = {name: _load_phase(paths[name], name) for name in PHASE_BUFFER}
    canonical = data["A1"]
    for name, doc in data.items():
        for field in ("model_tag", "model_digest", "dataset_sha256",
                      "max_tokens", "stream", "warmup_per_concurrency"):
            if doc.get(field) != canonical.get(field):
                raise PhaseRefused("phases_not_comparable")
        for a, b in zip(canonical["results"], doc["results"]):
            if a["requests"] != b["requests"]:
                raise PhaseRefused("phases_request_counts_differ")
    if len({str(path.resolve()) for path in paths.values()}) != 4:
        raise PhaseRefused("four_distinct_phase_files_required")
    # A/B/A/B is a time-ordered protocol, not four arbitrarily relabeled
    # reports. This still does NOT attest real process identity or thermals.
    stamps = []
    for name in PHASE_BUFFER:
        timestamp = data[name].get("observed_at_utc")
        if not isinstance(timestamp, str):
            raise PhaseRefused("phase_timestamp_missing")
        try:
            stamp = datetime.fromisoformat(timestamp)
        except ValueError:
            raise PhaseRefused("phase_timestamp_invalid") from None
        if stamp.tzinfo is None:
            raise PhaseRefused("phase_timestamp_missing_timezone")
        stamps.append(stamp)
    if any(older >= newer for older, newer in zip(stamps, stamps[1:])):
        raise PhaseRefused("phases_not_in_abab_chronological_order")
    by_c = {}
    for index, c in enumerate(LEVELS):
        values = {p: data[p]["results"][index] for p in PHASE_BUFFER}
        by_c[str(c)] = {
            "throughput_rps_by_phase": {
                p: values[p]["requests_per_second"] for p in PHASE_BUFFER
            },
            "p95_ttft_ms_by_phase": {
                p: values[p]["p95_ttft_ms_success_only"] for p in PHASE_BUFFER
            },
            "p95_latency_ms_by_phase": {
                p: values[p]["p95_latency_ms_success_only"] for p in PHASE_BUFFER
            },
            "failures_by_phase": {
                p: values[p]["failures"] for p in PHASE_BUFFER
            },
            "quality_verified": False,
        }
    return {
        "schema": "metriqen.sse-buffer-abab-review.v1",
        "order": list(PHASE_BUFFER),
        "by_concurrency": by_c,
        "within_setting_drift_requires_human_review": True,
        "runtime_binary_and_slots_human_attestation_required": True,
        "quality_human_review_required": True,
        "causal_speedup_verified": False,
        "p3_gate_approved": False,
        "note": "Four snapshots described only; no speedup, accuracy or statistical significance certified.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in AC-powered SSE buffer four-phase diagnostic")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="no requests, files, restarts or credentials")
    phase = sub.add_parser("collect", help="measure one already configured gateway phase")
    phase.add_argument("--execute", action="store_true")
    phase.add_argument("--phase", choices=tuple(PHASE_BUFFER), required=True)
    phase.add_argument("--model", default="llama3.2:1b")
    phase.add_argument("--expected-digest", required=True)
    phase.add_argument("--requests", type=int, default=12)
    phase.add_argument("--max-tokens", type=int, default=64)
    phase.add_argument("--output", type=Path, required=True)
    checker = sub.add_parser("review", help="offline descriptive four-phase report")
    for name in PHASE_BUFFER:
        checker.add_argument("--" + name.lower(), type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        print("PLAN ONLY: A1(1024) -> B1(4096) -> A2(1024) -> B2(4096)")
        print("One explicitly requested AC/digest/process-checked phase per invocation; never restarts gateway.")
        print("P3 quality, runtime slot/thermal attestation and speedup remain UNVERIFIED.")
        return 0
    if args.command == "collect" and not args.execute:
        print("NO ACTION: add --execute only after independently confirming AC and running gateway mode.")
        return 2
    try:
        if args.command == "collect":
            payload = collect(args.phase, args.model, args.expected_digest, args.output,
                              requests=args.requests, tokens=args.max_tokens)
            print("PRIVATE PHASE WRITTEN; P3 approval remains false; "
                  f"measurements={len(payload['results'])}")
        else:
            payload = review({name: getattr(args, name.lower()) for name in PHASE_BUFFER})
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError):
        print("SSE experiment refused or incomplete; no P3 acceptance claim.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
