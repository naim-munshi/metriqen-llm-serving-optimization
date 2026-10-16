"""Overload-aware local gateway experiments; no optimization claims or model simulation.

Unlike the strict 1/2/4 successful-request sweep, overload is an EXPECTED
outcome here. Preserve every submitted request and admission-counter delta.
Only a loopback endpoint is permitted. All run evidence is private by default.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import sys
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from tools.benchmark import runner

LEVELS = (4, 6, 8, 12)
COUNTERS = (
    "admitted_immediate_total", "admitted_queued_total",
    "rejected_full_total", "queue_timeout_total", "queue_canceled_total",
)
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def run_order(repeats: int) -> list[tuple[int, int]]:
    return [
        (repeat + 1, LEVELS[(offset + repeat) % len(LEVELS)])
        for repeat in range(repeats) for offset in range(len(LEVELS))
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def admission_snapshot(endpoint: str, expected_queue_depth: int) -> dict:
    """Fetch read-only aggregate counters from the same local gateway port."""
    runner.validate_endpoint(endpoint)
    address = urlsplit(endpoint)
    url = f"http://127.0.0.1:{address.port}/metrics/admission"
    opener = build_opener(ProxyHandler({}), runner.NoRedirect())
    with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=3) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("admission metrics content type is not JSON")
        raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError("admission metrics exceed bounded size")
        value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise ValueError("expected admission metrics schema v2")
    if (type(value.get("max_active_requests")) is not int
            or value["max_active_requests"] != 4
            or type(value.get("max_waiting_requests")) is not int
            or value["max_waiting_requests"] != expected_queue_depth):
        raise ValueError("gateway capacity/queue configuration does not match expected values")
    for key in COUNTERS:
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError(f"invalid admission counter: {key}")
    for key in ("active_requests", "waiting_requests"):
        if type(value.get(key)) is not int or value[key] != 0:
            raise ValueError(f"gateway is not idle: {key}")
    return {key: value[key] for key in COUNTERS}


def counter_delta(before: dict, after: dict) -> dict:
    delta = {key: after[key] - before[key] for key in COUNTERS}
    if any(value < 0 for value in delta.values()):
        raise ValueError("gateway admission counters reset during run; evidence invalid")
    return delta


def validate(args: argparse.Namespace) -> tuple[list, str]:
    runner.validate_endpoint(args.endpoint)
    if args.model != "llama3.2:1b":
        raise ValueError("this first overload protocol requires llama3.2:1b")
    if DIGEST.fullmatch(args.model_revision) is None:
        raise ValueError("model revision must be a verified 64-character lowercase digest")
    if args.expected_queue_depth not in (0, 2):
        raise ValueError("expected queue depth must be 0 or 2")
    if not 1 <= args.repeats <= 10 or not 12 <= args.requests <= 1000:
        raise ValueError("repeats must be 1-10 and requests 12-1000")
    if not 0 <= args.warmup <= 10 or not 1 <= args.max_tokens <= 2048:
        raise ValueError("warmup must be 0-10 and max-tokens 1-2048")
    if not 0 < args.timeout <= 180:
        raise ValueError("timeout must be in (0, 180]")
    if not args.runtime.strip() or not args.hardware.strip():
        raise ValueError("runtime/hardware descriptions are required")
    if args.output_dir.resolve() == args.dataset.resolve():
        raise ValueError("dataset and output directory must differ")
    return runner.read_dataset(args.dataset)


def summary(rows: list[dict]) -> dict:
    by_level = {}
    for level in LEVELS:
        selection = [row for row in rows if row["concurrency"] == level]
        if not selection:
            raise ValueError("missing concurrency level")
        request_count = sum(r["results"]["requests"] for r in selection)
        successes = sum(r["results"]["successes"] for r in selection)
        failures = sum(r["results"]["failures"] for r in selection)
        http_429 = sum(r["http_429_observed"] for r in selection)
        if request_count != successes + failures:
            raise ValueError("submitted requests do not reconcile")
        by_level[str(level)] = {
            "runs": len(selection),
            "submitted_requests": request_count,
            "successes": successes,
            "failures": failures,
            "http_429_observed": http_429,
            "success_rate": round(successes / request_count, 4),
            "goodput_rps_median": statistics.median(r["results"]["requests_per_second"] for r in selection),
            "successful_latency_p95_ms_run_median": (
                statistics.median(r["results"]["latency_ms"]["p95"] for r in selection
                                  if r["results"]["latency_ms"]["p95"] is not None)
                if any(r["results"]["latency_ms"]["p95"] is not None for r in selection) else None
            ),
            "successful_ttft_p95_ms_run_median": (
                statistics.median(r["results"]["ttft_ms"]["p95"] for r in selection
                                  if r["results"]["ttft_ms"]["p95"] is not None)
                if any(r["results"]["ttft_ms"]["p95"] is not None for r in selection) else None
            ),
            "admission_counter_deltas_including_warmup": {
                key: sum(r["admission_deltas_including_warmup"][key] for r in selection)
                for key in COUNTERS
            },
        }
    return by_level


def run(args: argparse.Namespace) -> Path:
    samples, dataset_digest = validate(args)
    # Check configuration BEFORE creating reports or producing inference traffic.
    admission_snapshot(args.endpoint, args.expected_queue_depth)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for ordinal, (repeat, concurrency) in enumerate(run_order(args.repeats), 1):
        before = admission_snapshot(args.endpoint, args.expected_queue_depth)
        started = utc_now()
        result = runner.run(
            args.endpoint, args.model, samples, stream=True,
            max_tokens=args.max_tokens, concurrency=concurrency,
            requests=args.requests, warmup=args.warmup, timeout=args.timeout,
            include_usage=not args.no_stream_usage,
        )
        finished = utc_now()
        after = admission_snapshot(args.endpoint, args.expected_queue_depth)
        deltas = counter_delta(before, after)
        observations = result.get("observations")
        if not isinstance(observations, list) or len(observations) != args.requests:
            raise ValueError("missing or truncated per-request overload evidence")
        actual_successes = sum(obs.get("success") is True for obs in observations)
        http_429 = sum(obs.get("status") == 429 for obs in observations)
        if (result["requests"] != args.requests
                or result["successes"] != actual_successes
                or result["failures"] != args.requests - actual_successes
                or result["requests_per_second"] is None):
            raise ValueError("runner outcome accounting is inconsistent")
        filename = f"repeat-{repeat:02d}-c{concurrency}.json"
        record = {
            "schema_version": 1,
            "repeat": repeat,
            "concurrency": concurrency,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "results": result,
            "http_429_observed": http_429,
            "admission_deltas_including_warmup": deltas,
        }
        with (args.output_dir / filename).open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
        records.append({key: value for key, value in record.items() if key != "results"}
                       | {"file": filename, "results": {
                           key: result[key] for key in ("requests", "successes", "failures",
                                                         "requests_per_second", "latency_ms", "ttft_ms")
                       }})
    manifest = {
        "schema_version": 1,
        "kind": "local_admission_overload_observation",
        "model_inference_attested": False,
        "performance_improvement_proven": False,
        "environment": {
            "endpoint": args.endpoint, "model": args.model,
            "model_revision_self_reported": args.model_revision,
            "runtime_self_reported": args.runtime,
            "hardware_self_reported": args.hardware,
            "dataset_sha256": dataset_digest,
        },
        "configuration": {
            "expected_queue_depth": args.expected_queue_depth, "concurrency_levels": list(LEVELS),
            "repeats": args.repeats, "requests_per_run": args.requests,
            "warmup_per_run": args.warmup, "max_tokens": args.max_tokens,
            "timeout_seconds": args.timeout,
            "stream_usage_requested": not args.no_stream_usage,
        },
        "run_files": records,
        "summary": summary(records),
        "limitations": [
            "Gateway queue depth is checked at runtime but the model digest, active Ollama slots, and hardware are not attested",
            "A client-observed HTTP 429 can originate from the gateway or the upstream; use admission counter deltas to distinguish",
            "Gateway counters include warmup requests and any unrelated traffic during each sampling window",
            "Successful-request p95 excludes failures; inspect the failure counts and latency of failed requests in raw local reports",
            "Fixed concurrency with a thread pool is not an arrival-rate-controlled or synchronized simultaneous burst",
            "This experiment does not prove causal optimization, statistical significance, GPU batching, or model quality",
        ],
    }
    destination = args.output_dir / "admission-summary.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preserve overload and admission outcomes on a loopback gateway")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--model", default="llama3.2:1b")
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--hardware", required=True)
    parser.add_argument("--dataset", type=Path, default=Path("docs/benchmarks/workload.v1.jsonl"))
    parser.add_argument("--expected-queue-depth", type=int, choices=(0, 2), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--requests", type=int, default=36)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--no-stream-usage", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        file = run(args)
        print(f"Saved overload observations (including failures): {file}")
        print("Evidence is descriptive, not a proven scheduler speedup.")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"admission overload benchmark rejected: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
