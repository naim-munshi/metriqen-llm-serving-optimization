#!/usr/bin/env python3
"""Offline release gate for evidence manifests; does not run or claim inference."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

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


def verify_report_contents(data: dict, manifest_path: Path) -> list[str]:
    """Check local report structure and consistency; never attest execution authenticity."""
    errors = []
    manifest_root = manifest_path.resolve().parent
    expected_model = data.get("model_tag")
    expected_revision = data.get("model_digest")
    expected_runtime = data.get("ollama_version")
    expected_hardware = data.get("hardware")
    expected_dataset = None
    observed_paths = set()
    runs = data.get("benchmark_reports")
    if not isinstance(runs, list):
        return ["benchmark_reports must be a list"]
    for index, entry in enumerate(runs):
        if not isinstance(entry, dict) or not isinstance(entry.get("report_path"), str):
            continue
        path = (manifest_root / entry["report_path"]).resolve()
        if path in observed_paths:
            errors.append(f"run {index}: duplicate report path")
            continue
        observed_paths.add(path)
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("report too large")
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("report must be an object")
            config, env, result = (report.get(k) for k in ("configuration", "environment", "results"))
            if not all(isinstance(x, dict) for x in (config, env, result)):
                raise ValueError("missing configuration, environment or results")
            if report.get("schema_version") != 1:
                raise ValueError("unsupported report schema")
            if type(config.get("concurrency")) is not int or config["concurrency"] != entry.get("concurrency"):
                raise ValueError("concurrency mismatch")
            if config.get("stream") is not True:
                raise ValueError("expected streaming baseline")
            if env.get("model") != expected_model or env.get("model_revision") != expected_revision:
                raise ValueError("model or revision mismatch")
            if env.get("runtime_self_reported") != expected_runtime or env.get("hardware_self_reported") != expected_hardware:
                raise ValueError("runtime or hardware mismatch")
            dataset_hash = env.get("dataset_sha256")
            if not isinstance(dataset_hash, str) or len(dataset_hash) != 64 or any(ch not in "0123456789abcdef" for ch in dataset_hash):
                raise ValueError("invalid dataset SHA-256")
            if expected_dataset is None:
                expected_dataset = dataset_hash
            elif expected_dataset != dataset_hash:
                raise ValueError("dataset mismatch across runs")
            n, ok, bad = (result.get(k) for k in ("requests", "successes", "failures"))
            if not all(type(x) is int and x >= 0 for x in (n, ok, bad)) or n < 1 or n != ok + bad:
                raise ValueError("inconsistent request counts")
            if n != config.get("requests"):
                raise ValueError("request count differs from configuration")
            observations = result.get("observations")
            if not isinstance(observations, list) or len(observations) != n:
                raise ValueError("observation count mismatch")
            if sum(isinstance(o, dict) and o.get("success") is True for o in observations) != ok:
                raise ValueError("success observation mismatch")
            if sum(isinstance(o, dict) and o.get("success") is False for o in observations) != bad:
                raise ValueError("failure observation mismatch")
            if bad:
                errors.append(f"run {index}: {bad} failed requests; investigate before acceptance")
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            errors.append(f"run {index}: invalid report ({type(exc).__name__}: {exc})")
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
