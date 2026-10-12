"""Construct a checked, human-attested manifest from a completed local sweep.

This module only reads already-created local reports. It never calls Ollama,
runs inference, infers hardware, or claims optimization performance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from tools.benchmark import evidence_gate, runner, sweep

MANIFEST_NAME = "evidence-manifest.json"
SUMMARY_NAME = "sweep-summary.json"
RUN_FILENAME = re.compile(r"repeat-(\d{2})-c(1|2|4)\.json\Z")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _utc_timestamp(text: str) -> str:
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", text):
        raise ValueError("timestamp-utc must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("timestamp-utc is not a valid UTC date/time") from None
    if parsed > datetime.now(timezone.utc):
        raise ValueError("timestamp-utc must not be in the future")
    return text


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _sweep_configuration(config: dict) -> int:
    repetitions = config.get("repeats")
    if type(repetitions) is not int or not 3 <= repetitions <= 10:
        raise ValueError("sweep must contain 3-10 repetitions")
    if config.get("concurrency") != [1, 2, 4]:
        raise ValueError("sweep concurrency must be [1, 2, 4]")
    if (type(config.get("stream_usage_requested")) is not bool
            or type(config.get("requests_per_run")) is not int
            or type(config.get("warmup_per_run")) is not int
            or type(config.get("max_tokens")) is not int
            or not 1 <= config["requests_per_run"] <= 1000
            or not 0 <= config["warmup_per_run"] <= 100
            or not 1 <= config["max_tokens"] <= 2048):
        raise ValueError("invalid sweep request configuration")
    if (type(config.get("timeout_seconds")) not in (int, float)
            or not 0 < config["timeout_seconds"] <= 180):
        raise ValueError("invalid sweep timeout")
    return repetitions


def collect_manifest(args: argparse.Namespace) -> tuple[dict, Path]:
    """Validate every input before creating an evidence manifest."""
    if not args.confirm_real_model_executed:
        raise ValueError("explicit --confirm-real-model-executed is required after manual hardware verification")
    root = args.sweep_dir.resolve()
    if not root.is_dir():
        raise ValueError("sweep directory does not exist")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ValueError("manifest already exists; do not overwrite previous evidence")

    summary, _ = evidence_gate._load_local_report(root, SUMMARY_NAME)
    env = _require_mapping(summary.get("environment"), "sweep environment")
    config = _require_mapping(summary.get("configuration"), "sweep configuration")
    repetitions = _sweep_configuration(config)
    digest = env.get("model_revision")
    if not isinstance(digest, str) or HEX_SHA256.fullmatch(digest) is None:
        raise ValueError("sweep must use a pinned 64-character lowercase model digest")
    for key in ("model", "runtime_self_reported", "hardware_self_reported", "dataset_sha256", "endpoint"):
        if not isinstance(env.get(key), str) or not env[key].strip():
            raise ValueError(f"invalid sweep environment field: {key}")
    if HEX_SHA256.fullmatch(env["dataset_sha256"]) is None:
        raise ValueError("invalid sweep dataset fingerprint")
    runner.validate_endpoint(env["endpoint"])

    items = summary.get("run_files")
    if not isinstance(items, list) or len(items) != 3 * repetitions:
        raise ValueError("sweep run list is incomplete")
    seen_pairs: set[tuple[int, int]] = set()
    seen_paths: set[Path] = set()
    reports = []
    computed_runs = []
    verifier_args = argparse.Namespace(
        endpoint=env["endpoint"], model=env["model"],
        model_revision=digest, runtime=env["runtime_self_reported"],
        hardware=env["hardware_self_reported"],
        requests=config["requests_per_run"],
        warmup=config["warmup_per_run"],
        max_tokens=config["max_tokens"],
        timeout=config["timeout_seconds"],
        no_stream_usage=not config["stream_usage_requested"],
    )
    for index, item in enumerate(items):
        entry = _require_mapping(item, f"run {index}")
        name, repeat, concurrency = (entry.get(key) for key in ("path", "repeat", "concurrency"))
        if (not isinstance(name, str) or RUN_FILENAME.fullmatch(name) is None
                or type(repeat) is not int or not 1 <= repeat <= repetitions
                or type(concurrency) is not int or concurrency not in (1, 2, 4)
                or name != f"repeat-{repeat:02d}-c{concurrency}.json"):
            raise ValueError(f"run {index}: filename / repeat / concurrency mismatch")
        if (repeat, concurrency) in seen_pairs:
            raise ValueError(f"run {index}: duplicate repetition and concurrency")
        seen_pairs.add((repeat, concurrency))
        report, path = evidence_gate._load_local_report(root, name)
        if path in seen_paths:
            raise ValueError(f"run {index}: duplicate report file")
        seen_paths.add(path)
        result = sweep._validate_run(report, verifier_args, concurrency, env["dataset_sha256"])
        reports.append({"concurrency": concurrency, "report_path": name})
        computed_runs.append({"concurrency": concurrency, "results": result})
    if len(seen_pairs) != 3 * repetitions:
        raise ValueError("missing run / concurrency combination")
    observed_summary = sweep.summarize_runs(computed_runs, argparse.Namespace(repeats=repetitions))
    if observed_summary != summary.get("summary"):
        raise ValueError("sweep aggregate metrics disagree with underlying run reports")

    # The quality report must be inside the same directory, not an external
    # URL, symlink escape or duplicate of the benchmark/aggregate reports.
    quality_label = args.quality_report
    if (not isinstance(quality_label, str)
            or not quality_label.endswith(".json")
            or quality_label in (MANIFEST_NAME, SUMMARY_NAME)):
        raise ValueError("quality-report must be a local JSON filename")
    quality, quality_path = evidence_gate._load_local_report(root, quality_label)
    if quality_path in seen_paths:
        raise ValueError("quality report duplicates a benchmark report")
    scoring = _require_mapping(quality.get("decoding"), "quality decoding")
    if (scoring.get("temperature") != 0 or scoring.get("stream") is not False
            or scoring.get("max_tokens") != config["max_tokens"]):
        raise ValueError("quality must use temperature 0 and matching max_tokens")
    quality_hash = quality.get("dataset_sha256")
    if not isinstance(quality_hash, str) or HEX_SHA256.fullmatch(quality_hash) is None:
        raise ValueError("quality dataset must have a SHA-256 fingerprint")

    if re.search(r"(?i)enter_actual|replace_with|example_value|placeholder", args.quantization):
        raise ValueError("quantization must be verified, not a documentation placeholder")
    timestamp = _utc_timestamp(args.timestamp_utc)
    manifest = {
        "model_tag": env["model"],
        "model_digest": digest,
        "ollama_version": env["runtime_self_reported"],
        "hardware": env["hardware_self_reported"],
        "host_os": args.host_os,
        "quantization": args.quantization,
        "timestamp_utc": timestamp,
        "notes": args.notes,
        "real_model_executed": True,  # Requires explicit human confirmation above.
        "benchmark_reports": reports,
        "quality_report": quality_label,
    }
    errors = evidence_gate.validate(manifest)
    if not errors:
        errors.extend(evidence_gate.verify_report_contents(manifest, manifest_path))
    if errors:
        # Validator emits only fixed field names, counts and error categories.
        raise ValueError("; ".join(errors[:3]))
    return manifest, manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate existing sweep + quality reports and build evidence manifest")
    parser.add_argument("--sweep-dir", type=Path, required=True)
    parser.add_argument("--quality-report", required=True,
                        help="JSON report file located inside the sweep directory")
    parser.add_argument("--host-os", required=True, help="OS and version observed by the operator")
    parser.add_argument("--quantization", required=True, help="Actual model quantization, verified by the operator")
    parser.add_argument("--timestamp-utc", required=True, help="UTC experiment timestamp, YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--notes", required=True, help="Short human-observed experiment context")
    parser.add_argument("--confirm-real-model-executed", action="store_true",
                        help="Human attestation only; does not cryptographically prove execution")
    args = parser.parse_args(argv)
    try:
        manifest, destination = collect_manifest(args)
        # Exclusive create protects even against a concurrent second invocation.
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, sort_keys=True, indent=2)
            handle.write("\n")
        print(f"Report consistency verified; human-attested manifest created: {destination}")
        print("CAUTION: report validation cannot prove hardware identity or real-model execution.")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"evidence bundle rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
