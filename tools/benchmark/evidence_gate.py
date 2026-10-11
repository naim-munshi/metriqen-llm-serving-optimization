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

def main(argv=None):
    parser = argparse.ArgumentParser(description="Check completeness of real-model evidence manifest")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        errors = validate(data)
    except (OSError, ValueError) as exc:
        print(f"manifest unreadable or invalid: {type(exc).__name__}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Manifest structure complete. Actual model execution and report authenticity are NOT verified.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
