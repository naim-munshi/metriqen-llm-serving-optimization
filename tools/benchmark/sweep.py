"""Repeat local-only streaming benchmarks without hiding failed measurements.

This orchestrator never makes model performance claims. It reuses runner.main
for each measured run and stores aggregate metrics only after every run passes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Any

from tools.benchmark import runner

CONCURRENCIES = (1, 2, 4)
MAX_REPEATS = 10


def validate_args(args: argparse.Namespace) -> tuple[str, Path]:
    """Fail fast before allocating output or starting inference."""
    runner.validate_endpoint(args.endpoint)
    if not 3 <= args.repeats <= MAX_REPEATS:
        raise ValueError("repeats must be between 3 and 10")
    if not 1 <= args.requests <= 1000:
        raise ValueError("requests must be between 1 and 1000")
    if not 0 <= args.warmup <= 100:
        raise ValueError("warmup must be between 0 and 100")
    if not 1 <= args.max_tokens <= 2048 or not 0 < args.timeout <= 180:
        raise ValueError("invalid max-tokens or timeout")
    if not isinstance(args.model, str) or not 1 <= len(args.model.strip()) <= 128:
        raise ValueError("model must be between 1 and 128 characters")
    if re.fullmatch(r"[0-9a-f]{64}", args.model_revision) is None:
        raise ValueError("model revision must be an exact lowercase 64-character model digest")
    if not all(isinstance(v, str) and v.strip() for v in (args.runtime, args.hardware)):
        raise ValueError("runtime and hardware must be specified")
    samples, sha = runner.read_dataset(args.dataset)
    if not samples:
        raise ValueError("dataset is empty")
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError("output directory already exists; use a new path to avoid overwriting evidence")
    if output == args.dataset.resolve() or output in args.dataset.resolve().parents:
        raise ValueError("output directory overlaps the dataset")
    return sha, output


def run_order(repeats: int) -> list[tuple[int, int]]:
    """Rotate load order each repeat to reduce systematic warm/cold bias."""
    return [(repeat + 1, CONCURRENCIES[(repeat + offset) % 3])
            for repeat in range(repeats) for offset in range(3)]


def _finite_nonnegative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _validate_run(report: dict, args: argparse.Namespace, concurrency: int,
                  fingerprint: str) -> dict[str, Any]:
    """Validate fields used in summary, rather than trusting a fake success code."""
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("invalid run schema")
    config, env, result = (report.get(name) for name in ("configuration", "environment", "results"))
    if not all(isinstance(item, dict) for item in (config, env, result)):
        raise ValueError("missing run configuration, environment, or results")
    expected_config = {
        "stream": True, "stream_usage_requested": not args.no_stream_usage,
        "requests": args.requests, "concurrency": concurrency,
        "warmup": args.warmup, "max_tokens": args.max_tokens,
        "timeout_seconds": args.timeout,
    }
    for key, value in expected_config.items():
        if config.get(key) != value:
            raise ValueError(f"run configuration mismatch: {key}")
    expected_env = {
        "endpoint": args.endpoint, "model": args.model,
        "model_revision": args.model_revision, "dataset_sha256": fingerprint,
        "runtime_self_reported": args.runtime,
        "hardware_self_reported": args.hardware,
    }
    if any(env.get(key) != value for key, value in expected_env.items()):
        raise ValueError("run environment mismatch")
    count, succeeded, failed = (result.get(key) for key in ("requests", "successes", "failures"))
    if (type(count) is not int or type(succeeded) is not int or type(failed) is not int
            or count != args.requests or succeeded != count or failed != 0):
        raise ValueError("run recorded unsuccessful requests")
    measurements = result.get("observations")
    if not isinstance(measurements, list) or len(measurements) != count:
        raise ValueError("per-request observations are missing")
    if any(not isinstance(item, dict) or item.get("success") is not True
           for item in measurements):
        raise ValueError("per-request observations include failures")
    for field in ("requests_per_second", "wall_seconds"):
        if not _finite_nonnegative(result.get(field)) or result[field] == 0:
            raise ValueError("invalid throughput or wall time")
    for field in ("latency_ms", "ttft_ms"):
        metric = result.get(field)
        if (not isinstance(metric, dict)
                or not all(_finite_nonnegative(metric.get(p)) for p in ("p50", "p95"))):
            raise ValueError(f"invalid {field} percentiles")
    coverage = result.get("completion_token_coverage")
    if type(coverage) is not int or not 0 <= coverage <= succeeded:
        raise ValueError("invalid token coverage")
    token_throughput = result.get("output_tokens_per_second")
    if coverage == succeeded:
        if (type(result.get("output_tokens")) is not int
                or result["output_tokens"] < 0
                or not _finite_nonnegative(token_throughput)):
            raise ValueError("invalid token throughput with complete usage")
    elif result.get("output_tokens") is not None or token_throughput is not None:
        raise ValueError("token throughput must be null when usage is incomplete")
    return result


def _range(numbers: list[float]) -> dict[str, float]:
    return {"median": round(statistics.median(numbers), 4),
            "min": round(min(numbers), 4), "max": round(max(numbers), 4)}


def summarize_runs(runs: list[dict], args: argparse.Namespace) -> dict:
    """Report run-level distributions, not invented p95 CIs or GPU metrics."""
    grouped: dict[int, list[dict]] = {c: [] for c in CONCURRENCIES}
    for run in runs:
        grouped[run["concurrency"]].append(run["results"])
    summary = {}
    for concurrency, records in grouped.items():
        if len(records) != args.repeats:
            raise ValueError("incomplete concurrency sweep")
        full_usage = all(r["completion_token_coverage"] == r["successes"] for r in records)
        summary[str(concurrency)] = {
            "runs": len(records),
            "successful_requests": sum(r["successes"] for r in records),
            "failed_requests": sum(r["failures"] for r in records),
            "requests_per_second": _range([r["requests_per_second"] for r in records]),
            "latency_run_p50_ms": _range([r["latency_ms"]["p50"] for r in records]),
            "latency_run_p95_ms": _range([r["latency_ms"]["p95"] for r in records]),
            "ttft_run_p50_ms": _range([r["ttft_ms"]["p50"] for r in records]),
            "ttft_run_p95_ms": _range([r["ttft_ms"]["p95"] for r in records]),
            "completion_token_coverage": sum(r["completion_token_coverage"] for r in records),
            "output_tokens_per_second": (
                _range([r["output_tokens_per_second"] for r in records]) if full_usage else None
            ),
        }
    return summary


def run(args: argparse.Namespace) -> Path:
    fingerprint, output = validate_args(args)
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for ordinal, (repeat, concurrency) in enumerate(run_order(args.repeats), 1):
        basename = f"repeat-{repeat:02d}-c{concurrency}.json"
        path = output / basename
        cli = ["--endpoint", args.endpoint, "--model", args.model,
               "--model-revision", args.model_revision,
               "--runtime", args.runtime, "--hardware", args.hardware,
               "--dataset", str(args.dataset), "--output", str(path),
               "--requests", str(args.requests), "--warmup", str(args.warmup),
               "--max-tokens", str(args.max_tokens), "--timeout", str(args.timeout),
               "--concurrency", str(concurrency), "--stream"]
        if args.no_stream_usage:
            cli.append("--no-stream-usage")
        print(f"Run {ordinal}/{3 * args.repeats}: concurrency={concurrency}, repeat={repeat}", flush=True)
        result_code = runner.main(cli)
        if result_code != 0:
            raise RuntimeError(f"run {ordinal} failed (runner exit {result_code}); partial reports retained at {output}")
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            summary = _validate_run(report, args, concurrency, fingerprint)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"run {ordinal} invalid ({type(exc).__name__}: {exc}); partial reports retained at {output}") from None
        records.append({"path": basename, "repeat": repeat,
                        "concurrency": concurrency, "results": summary})
    output_report = {
        "schema_version": 1,
        "method": "rotating-order repeated local client-observed streaming benchmarks",
        "environment": {"model": args.model, "model_revision": args.model_revision,
                        "runtime_self_reported": args.runtime,
                        "hardware_self_reported": args.hardware,
                        "dataset_sha256": fingerprint, "endpoint": args.endpoint},
        "configuration": {"repeats": args.repeats, "requests_per_run": args.requests,
                          "warmup_per_run": args.warmup, "concurrency": list(CONCURRENCIES),
                          "max_tokens": args.max_tokens, "timeout_seconds": args.timeout,
                          "stream_usage_requested": not args.no_stream_usage},
        "run_files": [{k: r[k] for k in ("path", "repeat", "concurrency")} for r in records],
        "summary": summarize_runs(records, args),
        "limitations": [
            "Run-level medians/ranges are not pooled request percentiles or confidence intervals",
            "Hardware, model and runtime identifiers are self-reported; execution is not attested",
            "Complete output-token throughput is unavailable unless every successful run reports usage",
            "No GPU utilization, model quality, pricing or optimization speedup is measured",
        ],
    }
    destination = output / "sweep-summary.json"
    destination.write_text(json.dumps(output_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run and summarize repeated local inference load tests")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True, help="64-character lowercase Ollama model digest")
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--hardware", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--no-stream-usage", action="store_true")
    args = parser.parse_args(argv)
    try:
        destination = run(args)
        print(f"Completed {args.repeats * 3} real-model run attempts; aggregate: {destination}")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"sweep error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
