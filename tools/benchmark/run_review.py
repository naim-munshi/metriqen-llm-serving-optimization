"""Offline per-run local benchmark reviewer; no inference or performance claims.

Reads raw run evidence, checks a repeated sweep's published aggregates, then
prints only safe, descriptive run metrics and large-variability screening flags.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from tools.benchmark import evidence_bundle, evidence_gate, run_timing, runner, sweep

WARNING_RATIO = 2.0


def _factor(values: list[float]) -> float | None:
    if not values:
        return None
    smallest = min(values)
    if smallest <= 0:
        return None
    return max(values) / smallest


def _metric(value: object, decimals: int = 2) -> str:
    if type(value) not in (int, float) or not math.isfinite(value):
        return "N/A"
    return f"{value:.{decimals}f}"


def review(sweep_dir: Path) -> dict:
    """Recompute all nine+ runs before using a summary for diagnostics."""
    root = sweep_dir.resolve()
    summary, _ = evidence_gate._load_local_report(root, "sweep-summary.json")
    env = evidence_bundle._require_mapping(summary.get("environment"), "sweep environment")
    config = evidence_bundle._require_mapping(summary.get("configuration"), "sweep configuration")
    repeats = evidence_bundle._sweep_configuration(config)
    identity = {}
    for field in ("model", "model_revision", "runtime_self_reported",
                  "hardware_self_reported", "dataset_sha256", "endpoint"):
        value = env.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("missing sweep identity")
        identity[field] = value
    if (evidence_bundle.HEX_SHA256.fullmatch(identity["model_revision"]) is None
            or evidence_bundle.HEX_SHA256.fullmatch(identity["dataset_sha256"]) is None):
        raise ValueError("invalid model/dataset fingerprints")
    runner.validate_endpoint(identity["endpoint"])

    entries = summary.get("run_files")
    if not isinstance(entries, list) or len(entries) != repeats * 3:
        raise ValueError("incomplete sweep run list")
    opts = argparse.Namespace(
        model=identity["model"], model_revision=identity["model_revision"],
        runtime=identity["runtime_self_reported"], hardware=identity["hardware_self_reported"],
        endpoint=identity["endpoint"], requests=config["requests_per_run"],
        warmup=config["warmup_per_run"], max_tokens=config["max_tokens"],
        timeout=config["timeout_seconds"], no_stream_usage=not config["stream_usage_requested"],
    )

    expected_order = sweep.run_order(repeats)
    computed = []
    rows = []
    for index, (entry, (repeat, concurrency)) in enumerate(zip(entries, expected_order), 1):
        if not isinstance(entry, dict) or (
                entry.get("repeat"), entry.get("concurrency"), entry.get("path")
        ) != (repeat, concurrency, f"repeat-{repeat:02d}-c{concurrency}.json"):
            raise ValueError(f"run {index}: sweep order or filename differs from rotating protocol")
        raw, _ = evidence_gate._load_local_report(root, entry["path"])
        result = sweep._validate_run(raw, opts, concurrency, identity["dataset_sha256"])
        if entry.get("timing") != raw.get("timing"):
            raise ValueError(f"run {index}: run timing summary differs from raw report")
        computed.append({"concurrency": concurrency, "results": result})
        rows.append({
            "ordinal": index, "repeat": repeat, "concurrency": concurrency,
            "requests_per_second": result["requests_per_second"],
            "ttft_p50_ms": result["ttft_ms"]["p50"],
            "latency_p95_ms": result["latency_ms"]["p95"],
            "output_tokens": result["output_tokens"],
            "wall_seconds": result["wall_seconds"],
            "started_at_utc": (
                entry["timing"]["started_at_utc"] if "timing" in entry else None),
        })
    run_timing.validate_sequence(entries)
    calculated = sweep.summarize_runs(computed, argparse.Namespace(repeats=repeats))
    if calculated != summary.get("summary"):
        raise ValueError("published sweep summary differs from raw run observations")

    warnings = []
    screening = {}
    for concurrency in sweep.CONCURRENCIES:
        group = [row for row in rows if row["concurrency"] == concurrency]
        rps_ratio = _factor([row["requests_per_second"] for row in group])
        ttft_ratio = _factor([row["ttft_p50_ms"] for row in group])
        tokens = [row["output_tokens"] for row in group]
        output_tokens_equal = all(isinstance(x, int) for x in tokens) and len(set(tokens)) == 1
        screening[str(concurrency)] = {
            "throughput_max_min_ratio": rps_ratio,
            "ttft_p50_max_min_ratio": ttft_ratio,
            "output_tokens_equal_between_repeats": output_tokens_equal,
        }
        for name, ratio in (("requests/sec", rps_ratio), ("TTFT p50", ttft_ratio)):
            if ratio is None:
                warnings.append(f"C={concurrency} {name}: zero or missing denominator; ratio unavailable")
            elif ratio >= WARNING_RATIO:
                warnings.append(
                    f"C={concurrency} {name}: {ratio:.2f}x maximum/minimum across repeats "
                    f"(descriptive {WARNING_RATIO:g}x screening threshold)")
    return {
        "model_revision": identity["model_revision"],
        "dataset_sha256": identity["dataset_sha256"],
        "rows": rows, "screening": screening, "warnings": warnings,
        "has_timing": all("timing" in entry for entry in entries),
        "repeats": repeats,
    }


def render(report: dict) -> str:
    lines = [
        "# Local inference sweep — per-run variability review",
        "",
        "**Classification:** descriptive screening only; not an optimization speedup, "
        "causal test, hardware attestation, or confidence interval.",
        "",
        f"- Model digest: \`{report['model_revision']}\`",
        f"- Workload SHA-256: \`{report['dataset_sha256']}\`",
        f"- Repeats per concurrency: {report['repeats']}",
        "- Timestamps: " + ("recorded by local runner" if report["has_timing"]
                             else "unavailable in legacy raw reports"),
        "- Quality and semantic equivalence: **not evaluated by this command**",
        "",
        "## Run order and metrics",
        "",
        "| Order | C | Repeat | RPS | TTFT p50 (ms) | Latency p95 (ms) | Output tokens | Measured wall (s) | Run start (UTC) |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["rows"]:
        tokens = row["output_tokens"]
        lines.append(
            f"| {row['ordinal']} | {row['concurrency']} | {row['repeat']} "
            f"| {_metric(row['requests_per_second'], 4)} "
            f"| {_metric(row['ttft_p50_ms'])} | {_metric(row['latency_p95_ms'])} "
            f"| {tokens if isinstance(tokens, int) else 'N/A'} "
            f"| {_metric(row['wall_seconds'])} "
            f"| {row['started_at_utc'] or 'Not recorded'} |"
        )
    lines.extend(["", "## Repeat variability", "",
                  "| C | RPS max/min | TTFT p50 max/min | Output-token totals equal |",
                  "| ---: | ---: | ---: | --- |"])
    for name, result in report["screening"].items():
        rps = result["throughput_max_min_ratio"]
        ttft = result["ttft_p50_max_min_ratio"]
        lines.append(
            f"| {name} | {rps:.2f}x" if rps is not None else f"| {name} | N/A"
        )
        lines[-1] += (f" | {ttft:.2f}x" if ttft is not None else " | N/A")
        lines[-1] += (" | Yes |" if result["output_tokens_equal_between_repeats"] else " | No/unknown |")
    lines.extend(["", "## Descriptive screening flags", ""])
    if report["warnings"]:
        lines.extend(f"- {flag}" for flag in report["warnings"])
    else:
        lines.append(f"No metric reached the {WARNING_RATIO:g}x range screening threshold.")
    lines.extend(["",
                  "The 2x max/min rule is a heuristic, **not** a statistical test, "
                  "outlier deletion policy, significance level, or evidence of causality.",
                  "Equal output-token totals do not establish equal prompt complexity, "
                  "cache conditions, GPU clock rate, temperature, or background load.",
                  "Raw reports and prompt data are not embedded in this review.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline repeated-sweep variability diagnosis")
    parser.add_argument("--sweep-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if output.suffix != ".md":
            raise ValueError("review output must have .md extension")
        result = review(args.sweep_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(render(result))
        print(f"Descriptive variability review created: {output}")
        print(f"Screening flags: {len(result['warnings'])}; no causal conclusions")
        return 0
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"Run review rejected: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
