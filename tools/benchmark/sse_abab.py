"""AC-powered, operator-invoked *descriptive* Go SSE buffer A/B/A/B.

One phase per explicit invocation; NEVER starts/stops a gateway, edits an
environment, provisions resources, reads credentials or auto-claims speedup.
Dry-run by default. Live mode only targets the existing loopback gateway.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
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
EXPERIMENT_BINARY = PRIVATE / "sse-abab-gateway"
DATASET = ROOT / "docs" / "benchmarks" / "workload.v1.jsonl"
ENDPOINT = "http://127.0.0.1:8081/v1/chat/completions"
EXPECTED_UPSTREAM = "http://127.0.0.1:11434"
PHASE_BUFFER = {"A1": 1024, "B1": 4096, "A2": 1024, "B2": 4096}
LEVELS = (1, 2, 4)
_ENV = re.compile(r"(?:^|\s)METRIQEN_SSE_RELAY_BUFFER_BYTES=([^\s]+)")
_UPSTREAM_ENV = re.compile(r"(?:^|\s)METRIQEN_UPSTREAM_URL=([^\s]+)")
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


def _verified_experiment_binary() -> tuple[Path, str]:
    """Require the one owner-built regular executable at the fixed private path.

    A file hash does not prove which Git source commit or bytes are mapped
    into RAM; the operator must still attest the build and runtime.
    """
    binary = EXPERIMENT_BINARY
    if (binary.is_symlink() or binary.parent != PRIVATE or
            not binary.is_file()):
        raise PhaseRefused("experiment_binary_missing_or_untrusted")
    st = binary.stat()
    if (not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o111 or
            not 0 < st.st_size <= 128 * 1024 * 1024):
        raise PhaseRefused("experiment_binary_not_executable_or_bounded")
    digest = hashlib.sha256()
    try:
        with binary.open("rb") as source:
            for block in iter(lambda: source.read(65536), b""):
                digest.update(block)
    except OSError:
        raise PhaseRefused("experiment_binary_unreadable") from None
    return binary.resolve(strict=True), digest.hexdigest()


def _macos_running_executable(pid: int) -> Path:
    """Ask the Darwin kernel for this PID's executable, not all mmap text vnodes.

    lsof -d txt may also list dylibs and other executable mappings. Matching
    one entry there alone does NOT establish the running main executable.
    """
    if type(pid) is not int or pid < 2:
        raise PhaseRefused("invalid_gateway_pid")
    try:
        # Only called by the explicitly macOS-only live operator path.
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        proc_pidpath = libproc.proc_pidpath
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        result = ctypes.create_string_buffer(4096)
        count = proc_pidpath(pid, result, len(result))
        if not 0 < count < len(result) or not result.value:
            raise PhaseRefused("mac_executable_identity_unavailable")
        return Path(os.fsdecode(result.value))
    except (OSError, AttributeError, TypeError, ValueError):
        raise PhaseRefused("mac_executable_identity_unavailable") from None


def inspect_gateway() -> dict:
    """Require the PID's actual executable, not merely its SSE environment."""

    raw = _capture(["lsof", "-nP", "-t", "-iTCP:8081", "-sTCP:LISTEN"])
    values = raw.split()
    if len(values) != 1 or not values[0].isdigit():
        raise PhaseRefused("expected_exactly_one_gateway_listener")
    pid = int(values[0])
    if pid < 2:
        raise PhaseRefused("invalid_gateway_pid")
    expected_binary, actual_sha = _verified_experiment_binary()
    # On macOS, lsof -F n reports the text vnode path for the running
    # executable. An old "go run" build-cache executable is NOT the
    # predeclared standalone binary, even if its env defaults to 1 KiB.
    files = _capture(["lsof", "-nP", "-a", "-p", str(pid),
                      "-d", "txt", "-F", "n"])
    text_paths = [line[1:] for line in files.splitlines() if line.startswith("n")]
    if str(expected_binary) not in text_paths:
        raise PhaseRefused("gateway_not_running_expected_binary")
    # macOS lsof txt includes mappings beyond the main executable. Verify the
    # PID's *actual executable* independently through libproc proc_pidpath.
    if _macos_running_executable(pid) != expected_binary:
        raise PhaseRefused("gateway_main_executable_mismatch")
    # Guard accidental public exposure during the operator experiment.
    listener = _capture(["lsof", "-nP", "-a", "-p", str(pid),
                         "-iTCP:8081", "-sTCP:LISTEN", "-F", "n"])
    if "n127.0.0.1:8081" not in listener.splitlines():
        raise PhaseRefused("gateway_listener_not_loopback")
    process = _capture(["ps", "eww", "-p", str(pid)])
    # Health only establishes some upstream answered. A different Ollama
    # service could otherwise skew a real comparison while sharing this Go
    # binary. Require exact observed process config, without logging ps eww.
    origins = _UPSTREAM_ENV.findall(process)
    if origins != [EXPECTED_UPSTREAM]:
        raise PhaseRefused("gateway_upstream_origin_not_verified")
    found = _ENV.findall(process)
    if len(found) > 1 or (found and found[0] != "4096"):
        raise PhaseRefused("unsupported_running_sse_setting")
    return {
        "pid": pid,
        "buffer_bytes": 4096 if found else 1024,
        "evidence": "process_env_4096" if found else "env_unset_assumed_default",
        "binary_path_verified": True,
        "mac_proc_pidpath_verified": True,
        "binary_sha256": actual_sha,
        "upstream_origin_verified": True,
        "upstream_origin": EXPECTED_UPSTREAM,
        # Hash and vnode identify the owner-built binary, but cannot
        # independently prove its source SHA or its mapped image bytes.
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
                observation["successes"] < 0 or observation["failures"] < 0 or
                observation["successes"] + observation["failures"] != requests):
            raise PhaseRefused("invalid_runner_request_accounting")
        coverage = observation.get("completion_token_coverage")
        if (type(coverage) is not int or
                not 0 <= coverage <= observation["successes"]):
            raise PhaseRefused("invalid_runner_completion_token_coverage")
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
        "observed_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
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
    process = data.get("gateway_process")
    expected_evidence = (
        "process_env_4096" if PHASE_BUFFER[expected] == 4096
        else "env_unset_assumed_default"
    )
    if (not isinstance(process, dict) or
            type(process.get("pid")) is not int or process["pid"] < 2 or
            process.get("buffer_bytes") != PHASE_BUFFER[expected] or
            process.get("evidence") != expected_evidence or
            process.get("upstream_origin_verified") is not True or
            process.get("upstream_origin") != EXPECTED_UPSTREAM or
            process.get("binary_path_verified") is not True or
            process.get("mac_proc_pidpath_verified") is not True or
            not isinstance(process.get("binary_sha256"), str) or
            _SHA.fullmatch(process["binary_sha256"]) is None or
            process.get("binary_source_sha_verified") is not False):
        raise PhaseRefused("active_gateway_binary_not_verified")
    vals = data.get("results")
    if (not isinstance(vals, list) or len(vals) != 3 or
            [x.get("concurrency") for x in vals if isinstance(x, dict)] != list(LEVELS)):
        raise PhaseRefused("phase_result_shape_invalid")
    for v in vals:
        count, success, failure = (v.get(k) for k in ("requests", "successes", "failures"))
        if (any(type(x) is not int or x < 0 for x in (count, success, failure))
                or count < 8 or count > 36 or success + failure != count):
            raise PhaseRefused("phase_request_accounting_invalid")
        coverage = v.get("completion_token_coverage")
        if type(coverage) is not int or not 0 <= coverage <= success:
            raise PhaseRefused("phase_completion_token_coverage_invalid")
        for k in ("requests_per_second", "p95_latency_ms_success_only",
                  "p95_ttft_ms_success_only"):
            n = v.get(k)
            if n is not None and (type(n) not in (int, float) or
                                  not 0 <= n < 10**9):
                raise PhaseRefused("phase_nonfinite_metric")
        rps = v["requests_per_second"]
        latency = v["p95_latency_ms_success_only"]
        ttft = v["p95_ttft_ms_success_only"]
        # A successful streaming request always has a first content token;
        # the runner also returns a latency for every successful request.
        if (rps is None or
                (success and (rps <= 0 or latency is None or ttft is None)) or
                (not success and (rps != 0 or latency is not None or ttft is not None))):
            raise PhaseRefused("phase_success_metrics_inconsistent")
    return data


def _relative_change_pct(first: float | None, last: float | None) -> float | None:
    """Descriptive later-vs-earlier drift only; zero/missing denominators are N/A."""
    if first is None or last is None or first <= 0:
        return None
    return round((last - first) * 100.0 / first, 4)


def review(paths: dict[str, Path]) -> dict:
    if set(paths) != set(PHASE_BUFFER):
        raise PhaseRefused("four_phases_required")
    data = {name: _load_phase(paths[name], name) for name in PHASE_BUFFER}
    canonical = data["A1"]
    original_binary_sha = canonical["gateway_process"]["binary_sha256"]
    for name, doc in data.items():
        if doc["gateway_process"]["binary_sha256"] != original_binary_sha:
            raise PhaseRefused("gateway_binary_differs_across_abab_phases")
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
        drift_metrics = {
            "throughput_rps": "requests_per_second",
            "p95_ttft_ms_success_only": "p95_ttft_ms_success_only",
            "p95_latency_ms_success_only": "p95_latency_ms_success_only",
        }
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
            "successes_by_phase": {
                p: values[p]["successes"] for p in PHASE_BUFFER
            },
            "failures_by_phase": {
                p: values[p]["failures"] for p in PHASE_BUFFER
            },
            "completion_token_coverage_by_phase": {
                p: values[p]["completion_token_coverage"] for p in PHASE_BUFFER
            },
            "missing_completion_usage_by_phase": {
                p: values[p]["successes"] - values[p]["completion_token_coverage"]
                for p in PHASE_BUFFER
            },
            "within_setting_drift_pct": {
                label: {
                    "A1_to_A2": _relative_change_pct(values["A1"][key], values["A2"][key]),
                    "B1_to_B2": _relative_change_pct(values["B1"][key], values["B2"][key]),
                }
                for label, key in drift_metrics.items()
            },
            "quality_verified": False,
        }
    return {
        "schema": "metriqen.sse-buffer-abab-review.v1",
        "order": list(PHASE_BUFFER),
        "by_concurrency": by_c,
        "within_setting_drift_requires_human_review": True,
        "incomplete_provider_token_usage_requires_human_review": any(
            vals["missing_completion_usage_by_phase"][phase] > 0
            for vals in by_c.values() for phase in PHASE_BUFFER
        ),
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
