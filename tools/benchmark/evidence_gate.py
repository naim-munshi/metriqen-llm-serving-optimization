#!/usr/bin/env python3
"""Offline release gate for evidence manifests; does not run or claim inference."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from tools.benchmark import run_timing

REQUIRED = ("model_tag", "model_digest", "ollama_version", "host_os",
            "hardware", "quantization", "timestamp_utc", "notes")
PLACEHOLDERS = ("replace", "todo", "unknown", "tbd", "example", "fixture", "mock")

def validate(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    errors = []
    for key in REQUIRED:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key}: nonempty string required")
        elif any(token in value.lower() for token in PLACEHOLDERS):
            errors.append(f"{key}: placeholder values are not accepted")
    if data.get("real_model_executed") is not True:
        errors.append("real_model_executed must be true after actual inference")
    runs = data.get("benchmark_reports")
    if not isinstance(runs, list) or len(runs) < 9:
        errors.append("benchmark_reports requires at least 9 run entries (3 repeats x 3 concurrencies)")
    else:
        by_concurrency = {1: 0, 2: 0, 4: 0}
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(f"run {index}: object required")
                continue
            c = run.get("concurrency")
            if type(c) is not int or c not in by_concurrency:
                errors.append(f"run {index}: concurrency must be 1, 2 or 4")
            else:
                by_concurrency[c] += 1
            if not isinstance(run.get("report_path"), str) or not run["report_path"].strip():
                errors.append(f"run {index}: report_path required")
        for c, count in by_concurrency.items():
            if count < 3:
                errors.append(f"concurrency {c}: requires at least 3 runs")
    quality = data.get("quality_report")
    if not isinstance(quality, str) or not quality.strip():
        errors.append("quality_report path required")
    return errors



def _is_nonnegative_number(value):
    import math
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _nearest_rank(values, quantile):
    import math
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


def _load_local_report(root, label):
    """Only read bounded local report files below the manifest's directory."""
    if not isinstance(label, str) or not label.strip():
        raise ValueError("missing report path")
    path = (root / label).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("report must be a local file within the manifest directory")
    if not 0 < path.stat().st_size <= 4 * 1024 * 1024:
        raise ValueError("report size invalid")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise ValueError("unsupported report schema")
    return record, path


def _check_quality_report(report, manifest):
    for key, wanted in (
        ("model", manifest["model_tag"]),
        ("model_revision_self_reported", manifest["model_digest"]),
        ("runtime_self_reported", manifest["ollama_version"]),
        ("hardware_self_reported", manifest["hardware"]),
    ):
        if report.get(key) != wanted:
            raise ValueError(f"quality {key} mismatch")
    result = report.get("results")
    if not isinstance(result, dict) or not isinstance(result.get("quality"), dict):
        raise ValueError("quality results missing")
    quality = result["quality"]
    total = quality.get("sample_count")
    if type(total) is not int or total <= 0:
        raise ValueError("invalid quality sample count")
    for name in ("exact_match", "normalized_exact_match"):
        item = quality.get(name)
        if not isinstance(item, dict):
            raise ValueError(f"{name} missing")
        n, ratio = item.get("correct"), item.get("rate")
        if type(n) is not int or not 0 <= n <= total:
            raise ValueError(f"{name} invalid count")
        if not _is_nonnegative_number(ratio) or abs(ratio - n / total) > 1e-6:
            raise ValueError(f"{name} inconsistent rate")
    if quality["exact_match"]["correct"] > quality["normalized_exact_match"]["correct"]:
        raise ValueError("normalized matches cannot be fewer than exact matches")
    generation = result.get("generation")
    if (not isinstance(generation, dict)
            or generation.get("successful_requests") != total
            or generation.get("failed_requests") != 0):
        raise ValueError("quality generation counts inconsistent")


def verify_report_contents(data: dict, manifest_path: Path) -> list[str]:
    """Validate report consistency; never attest genuine model execution."""
    import math
    import re
    errors = []
    root = manifest_path.resolve().parent
    seen_paths = set()
    dataset_hash = None
    common_profile = None
    for index, entry in enumerate(data.get("benchmark_reports", [])):
        if not isinstance(entry, dict):
            errors.append(f"run {index}: invalid manifest entry")
            continue
        try:
            report, path = _load_local_report(root, entry.get("report_path"))
            if path in seen_paths:
                raise ValueError("duplicate report path")
            seen_paths.add(path)
            config, env, result = (report.get(k) for k in ("configuration", "environment", "results"))
            if not all(isinstance(v, dict) for v in (config, env, result)):
                raise ValueError("report lacks configuration, environment, or results")
            if type(config.get("concurrency")) is not int or config["concurrency"] != entry.get("concurrency"):
                raise ValueError("concurrency mismatch")
            if config.get("stream") is not True:
                raise ValueError("streaming benchmark required")
            if env.get("model") != data["model_tag"] or env.get("model_revision") != data["model_digest"]:
                raise ValueError("model / revision mismatch")
            if (env.get("runtime_self_reported") != data["ollama_version"]
                    or env.get("hardware_self_reported") != data["hardware"]):
                raise ValueError("runtime / hardware mismatch")
            sha = env.get("dataset_sha256")
            if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
                raise ValueError("invalid dataset fingerprint")
            if dataset_hash is not None and dataset_hash != sha:
                raise ValueError("workload dataset differs between runs")
            dataset_hash = sha
            profile = {k: config.get(k) for k in ("requests", "warmup", "max_tokens", "timeout_seconds")}
            if common_profile is not None and profile != common_profile:
                raise ValueError("workload configuration differs between runs")
            common_profile = profile
            n, good, bad = (result.get(k) for k in ("requests", "successes", "failures"))
            if not all(type(v) is int and v >= 0 for v in (n, good, bad)) or n < 1 or good + bad != n:
                raise ValueError("inconsistent request counts")
            if "timing" in report:
                run_timing.validate(report["timing"], result.get("wall_seconds"))
            if config.get("requests") != n:
                raise ValueError("requested / observed counts differ")
            items = result.get("observations")
            if not isinstance(items, list) or len(items) != n:
                raise ValueError("observation count mismatch")
            seen_indices = set()
            latencies, ttfts = [], []
            completion_count = 0
            tokens = 0
            failures = 0
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("invalid observation")
                i = item.get("index")
                if type(i) is not int or not 0 <= i < n or i in seen_indices:
                    raise ValueError("duplicate or invalid observation index")
                seen_indices.add(i)
                if not _is_nonnegative_number(item.get("latency_ms")) or type(item.get("success")) is not bool:
                    raise ValueError("invalid observation latency / outcome")
                if item["success"]:
                    if item.get("status") != 200 or item.get("error") is not None:
                        raise ValueError("non-200 response marked successful")
                    latencies.append(item["latency_ms"])
                    ttft = item.get("ttft_ms")
                    if not _is_nonnegative_number(ttft) or ttft > item["latency_ms"] + 1:
                        raise ValueError("invalid TTFT")
                    ttfts.append(ttft)
                    usage = item.get("completion_tokens")
                    if usage is not None:
                        if type(usage) is not int or usage < 0:
                            raise ValueError("invalid completion tokens")
                        completion_count += 1
                        tokens += usage
                else:
                    failures += 1
                    if not isinstance(item.get("error"), str) or not item["error"]:
                        raise ValueError("failure has no error classification")
            if good != len(latencies) or bad != failures:
                raise ValueError("observations disagree with summary")
            if result.get("ttft_samples") != len(ttfts):
                raise ValueError("incorrect TTFT coverage")
            if result.get("completion_token_coverage") != completion_count:
                raise ValueError("incorrect token usage coverage")
            if result.get("output_tokens") != (tokens if good and completion_count == good else None):
                raise ValueError("incorrect completion-token total")
            for metric, samples in (("latency_ms", latencies), ("ttft_ms", ttfts)):
                if samples:
                    summary = result.get(metric)
                    if not isinstance(summary, dict):
                        raise ValueError(f"missing {metric} summary")
                    for quantile, label in ((.5, "p50"), (.95, "p95")):
                        value = summary.get(label)
                        expected = _nearest_rank(samples, quantile)
                        if not _is_nonnegative_number(value) or abs(value - expected) > max(.02, expected * .0001):
                            raise ValueError(f"incorrect {metric} {label}")
            if bad:
                raise ValueError(f"{bad} failed requests; preserve report and investigate")
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            errors.append(f"run {index}: {exc}")
    try:
        report, path = _load_local_report(root, data.get("quality_report"))
        if path in seen_paths:
            raise ValueError("quality report duplicates benchmark report")
        _check_quality_report(report, data)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        errors.append(f"quality: {exc}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check completeness of real-model evidence manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--verify-reports", action="store_true", help="Inspect benchmark report contents, not just manifest structure")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        errors = validate(data)
        if args.verify_reports and not errors:
            errors.extend(verify_report_contents(data, args.manifest))
    except (OSError, ValueError) as exc:
        print(f"manifest unreadable or invalid: {type(exc).__name__}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Evidence structure and requested report checks passed. Real execution and authenticity are NOT verified.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
