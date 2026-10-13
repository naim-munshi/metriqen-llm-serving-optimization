"""Audit a published aggregate transcription against PRIVATE local evidence.

This CLI loads both verified evidence bundles through the existing strict
comparator; it does not run inference or publish prompts/responses.
Data consistency cannot attest provenance, hardware or causality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from tools.benchmark import compare

ROOT = Path(__file__).resolve().parents[2]
STUDY_KIND = "developer_transcribed_real_local_aggregate"
METRICS = (
    "requests_per_second", "output_tokens_per_second",
    "latency_run_p95_ms", "ttft_run_p50_ms",
)
DATASETS = frozenset({
    "docs/benchmarks/workload.v1.jsonl",
    "docs/nlp-eval/workload.v1.jsonl",
})


def _equal(actual: object, expected: object, label: str) -> None:
    """Exact match including scalar types (do not silently allow booleans)."""
    if type(actual) is not type(expected) or actual != expected:
        raise ValueError(f"{label}: does not match locally checked reports")


def _number(actual: object, expected: object, label: str, *, tolerance: float = 0.00005) -> None:
    if (type(actual) not in (float, int)
            or not math.isfinite(actual)
            or not math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance)):
        raise ValueError(f"{label}: disagrees with locally checked numeric evidence")


def _check_versioned_dataset(label: object, expected_digest: str) -> None:
    """Verify published v1 dataset filenames still have the measured bytes."""
    if not isinstance(label, str) or label not in DATASETS:
        raise ValueError("invalid or unexpected versioned dataset name")
    data = (ROOT / label).read_bytes()
    if not 0 < len(data) <= 1024 * 1024:
        raise ValueError("versioned dataset has an invalid file size")
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError("versioned dataset fingerprint differs from measured local reports")


def audit(published_path: Path, baseline_path: Path, candidate_path: Path) -> None:
    """Raise on any divergence; never write raw, private or public artifacts."""
    if not published_path.is_file() or not 0 < published_path.stat().st_size <= 1024 * 1024:
        raise ValueError("published aggregate missing or oversized")
    published = json.loads(published_path.read_text(encoding="utf-8"))
    if not isinstance(published, dict):
        raise ValueError("published aggregate must be a JSON object")
    _equal(published.get("schema_version"), 1, "published schema")
    _equal(published.get("kind"), STUDY_KIND, "record type")

    # Recompute both experiment summaries from original local raw reports;
    # this reuses the strict manifest, raw-percentile, run-count and quality
    # validation gates rather than trusting the published JSON.
    verified = compare.compare(
        baseline_path, candidate_path,
        "Auditing reported local Ollama inference parallelism experiment",
    )
    baseline = compare._read_experiment(baseline_path)
    candidate = compare._read_experiment(candidate_path)
    provenance = published["provenance"]
    _equal(provenance["comparator_status"], verified["comparison_status"], "comparison classification")
    _equal(provenance["github_ci_revalidates_raw_hardware_reports"], False, "provenance scope")
    _equal(published["model"]["tag"], baseline["manifest"]["model_tag"], "model tag")
    _equal(published["model"]["digest"], verified["model_digest"], "model digest")
    _equal(published["model"]["quantization"], baseline["manifest"]["quantization"], "quantization")

    parameters = published["methodology"]
    config = baseline["configuration"]
    checks = (
        ("concurrency_levels", config["concurrency"]),
        ("repeats_per_concurrency", config["repeats"]),
        ("measured_requests_per_run", config["requests_per_run"]),
        ("warmup_requests_per_run", config["warmup_per_run"]),
        ("max_completion_tokens", config["max_tokens"]),
    )
    for key, expected in checks:
        _equal(parameters[key], expected, f"protocol {key}")
    _number(parameters["timeout_seconds"], config["timeout_seconds"], "protocol timeout_seconds")
    _equal(parameters["temperature"], 0, "published temperature")
    _equal(parameters["streaming"], True, "published streaming")
    _equal(parameters["baseline_ollama_num_parallel"], 1, "declared baseline mode")
    _equal(parameters["candidate_ollama_num_parallel"], 2, "declared candidate mode")
    _check_versioned_dataset(parameters["benchmark_dataset"], verified["benchmark_dataset_sha256"])
    _check_versioned_dataset(parameters["quality_dataset"], verified["quality_dataset_sha256"])

    count_per_setting = config["repeats"] * config["requests_per_run"] * 3
    _equal(parameters["requests_per_configuration"], count_per_setting, "requests per setting")
    _equal(parameters["total_measured_requests_across_both_configurations"],
           count_per_setting * 2, "combined request count")

    samples = verified["quality"]["samples"]
    for label in ("baseline", "candidate"):
        info = published["quality"][label]
        _equal(info["samples"], samples, f"{label} quality sample count")
        _equal(info["successful_requests"], samples, f"{label} quality success count")
        _equal(info["failed_requests"], 0, f"{label} quality failure count")
        for local, public in (("exact_match", "exact_correct"),
                              ("normalized_exact_match", "normalized_exact_correct")):
            correct = round(verified["quality"][label][local] * samples)
            _equal(info[public], correct, f"{label} quality {local}")
    # Compare all observations represented in the sanitized transcription.
    levels = published["metrics_by_concurrency"]
    _equal(set(levels), set(verified["metrics_by_concurrency"]), "concurrency coverage")
    for c, item in verified["metrics_by_concurrency"].items():
        public = levels[c]
        _equal(public["successful_requests_per_configuration"],
               baseline["summary"][c]["successful_requests"], f"c{c} successful requests baseline")
        _equal(public["successful_requests_per_configuration"],
               candidate["summary"][c]["successful_requests"], f"c{c} successful requests candidate")
        _equal(public["failed_requests_per_configuration"], 0, f"c{c} failed requests")
        _equal(baseline["summary"][c]["failed_requests"], 0, f"c{c} baseline failures")
        _equal(candidate["summary"][c]["failed_requests"], 0, f"c{c} candidate failures")
        for metric in METRICS:
            comparison = item[metric]
            _number(public["baseline"][metric], comparison["baseline_median"], f"c{c} {metric} baseline")
            _number(public["candidate"][metric], comparison["candidate_median"], f"c{c} {metric} candidate")
            _number(public["percentage_change"][metric],
                    comparison["percentage_change"], f"c{c} {metric} change", tolerance=0.0005)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit publicly transcribed figures against complete private local benchmark evidence")
    parser.add_argument("--published", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        audit(args.published, args.baseline, args.candidate)
        print("Published aggregate arithmetic and workload fingerprints match locally verified reports.")
        print("Scope: consistency only; machine authenticity, active parallelism and causality NOT attested.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        # Do not echo arbitrary private paths, user-supplied notes or report bodies.
        print("Published aggregate audit rejected: invalid, mismatched or incomplete local evidence.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
