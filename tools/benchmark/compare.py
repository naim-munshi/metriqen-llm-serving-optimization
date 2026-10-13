"""Compare two locally observed, human-attested serving experiments.

This is an offline consistency and descriptive-delta tool, NOT a causal
speedup test or real-model/GPU attestation. No inference or network calls.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from tools.benchmark import evidence_bundle, evidence_gate, runner, sweep


def _read_experiment(filename: Path) -> dict:
    """Revalidate all raw run data and its sweep summary before comparison."""
    manifest_file = filename.resolve()
    if not manifest_file.is_file() or manifest_file.stat().st_size > 1024 * 1024:
        raise ValueError("evidence manifest is missing or oversized")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    problems = evidence_gate.validate(manifest)
    if not problems:
        problems = evidence_gate.verify_report_contents(manifest, manifest_file)
    if problems:
        raise ValueError("evidence manifest or raw reports failed verification")

    root = manifest_file.parent
    summary, _ = evidence_gate._load_local_report(root, "sweep-summary.json")
    env = evidence_bundle._require_mapping(summary.get("environment"), "sweep environment")
    config = evidence_bundle._require_mapping(summary.get("configuration"), "sweep configuration")
    repeats = evidence_bundle._sweep_configuration(config)
    expected_env = {
        "model": manifest["model_tag"],
        "model_revision": manifest["model_digest"],
        "runtime_self_reported": manifest["ollama_version"],
        "hardware_self_reported": manifest["hardware"],
    }
    if any(env.get(k) != v for k, v in expected_env.items()):
        raise ValueError("sweep and manifest identity mismatch")
    if not isinstance(env.get("dataset_sha256"), str):
        raise ValueError("missing benchmark dataset fingerprint")
    runner.validate_endpoint(env.get("endpoint", ""))

    run_entries = summary.get("run_files")
    declared = manifest.get("benchmark_reports")
    if (not isinstance(run_entries, list) or len(run_entries) != 3 * repeats
            or not isinstance(declared, list) or len(declared) != len(run_entries)):
        raise ValueError("incomplete or inconsistent run lists")
    declared_pairs = {(e.get("concurrency"), e.get("report_path")) for e in declared}
    if len(declared_pairs) != len(declared):
        raise ValueError("duplicate manifest run records")
    check_args = argparse.Namespace(
        model=env["model"], model_revision=env["model_revision"],
        runtime=env["runtime_self_reported"],
        hardware=env["hardware_self_reported"], endpoint=env["endpoint"],
        requests=config["requests_per_run"], warmup=config["warmup_per_run"],
        max_tokens=config["max_tokens"], timeout=config["timeout_seconds"],
        no_stream_usage=not config["stream_usage_requested"],
    )
    calculated = []
    seen = set()
    for entry in run_entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid run list entry")
        repeat, concurrency, label = (entry.get(k) for k in ("repeat", "concurrency", "path"))
        if (type(repeat) is not int or not 1 <= repeat <= repeats
                or type(concurrency) is not int or concurrency not in (1, 2, 4)
                or label != f"repeat-{repeat:02d}-c{concurrency}.json"
                or (repeat, concurrency) in seen
                or (concurrency, label) not in declared_pairs):
            raise ValueError("invalid, missing, or duplicate run identity")
        seen.add((repeat, concurrency))
        raw, _ = evidence_gate._load_local_report(root, label)
        result = sweep._validate_run(raw, check_args, concurrency, env["dataset_sha256"])
        calculated.append({"concurrency": concurrency, "results": result})
    if len(seen) != 3 * repeats:
        raise ValueError("missing concurrency repetition")
    actual_summary = sweep.summarize_runs(calculated, argparse.Namespace(repeats=repeats))
    if actual_summary != summary.get("summary"):
        raise ValueError("summary disagrees with raw experiment evidence")

    quality, _ = evidence_gate._load_local_report(root, manifest["quality_report"])
    quality_decoding = quality.get("decoding")
    if (not isinstance(quality_decoding, dict)
            or quality_decoding != {"temperature": 0, "max_tokens": config["max_tokens"], "stream": False}):
        raise ValueError("quality decoding does not match benchmark settings")
    quality_hash = quality.get("dataset_sha256")
    if not isinstance(quality_hash, str) or evidence_bundle.HEX_SHA256.fullmatch(quality_hash) is None:
        raise ValueError("quality dataset fingerprint is invalid")
    result = quality["results"]["quality"]
    return {
        "manifest": manifest, "environment": env, "configuration": config,
        "summary": actual_summary, "quality_dataset_sha256": quality_hash,
        "quality_decoding": quality_decoding,
        "quality_samples": result["sample_count"],
        "quality": {
            "exact_match": result["exact_match"]["rate"],
            "normalized_exact_match": result["normalized_exact_match"]["rate"],
        },
    }


def _percent_change(before: float, after: float) -> float | None:
    if not all(isinstance(v, (float, int)) and math.isfinite(v) and v >= 0
               for v in (before, after)) or before == 0:
        return None
    return round((after / before - 1) * 100, 3)


def compare(baseline_path: Path, candidate_path: Path, change: str,
            *, min_quality_samples: int = 30, min_requests_per_run: int = 30) -> dict:
    if not isinstance(change, str) or not 3 <= len(change.strip()) <= 160:
        raise ValueError("describe the single changed serving setting in 3-160 characters")
    if not 1 <= min_quality_samples <= 10000 or not 1 <= min_requests_per_run <= 1000:
        raise ValueError("invalid minimum sample requirements")
    if baseline_path.resolve() == candidate_path.resolve():
        raise ValueError("comparison requires distinct baseline and candidate experiments")
    baseline = _read_experiment(baseline_path)
    candidate = _read_experiment(candidate_path)

    checks = {
        "model tag": (baseline["manifest"]["model_tag"], candidate["manifest"]["model_tag"]),
        "model digest": (baseline["manifest"]["model_digest"], candidate["manifest"]["model_digest"]),
        "host OS": (baseline["manifest"]["host_os"], candidate["manifest"]["host_os"]),
        "hardware": (baseline["manifest"]["hardware"], candidate["manifest"]["hardware"]),
        "quantization": (baseline["manifest"]["quantization"], candidate["manifest"]["quantization"]),
        "runtime version": (baseline["manifest"]["ollama_version"], candidate["manifest"]["ollama_version"]),
        "gateway endpoint": (baseline["environment"]["endpoint"], candidate["environment"]["endpoint"]),
        "benchmark dataset": (baseline["environment"]["dataset_sha256"], candidate["environment"]["dataset_sha256"]),
        "quality dataset": (baseline["quality_dataset_sha256"], candidate["quality_dataset_sha256"]),
        "quality decoding": (baseline["quality_decoding"], candidate["quality_decoding"]),
    }
    for key in ("concurrency", "repeats", "requests_per_run", "warmup_per_run",
                "max_tokens", "timeout_seconds", "stream_usage_requested"):
        checks[f"sweep {key}"] = (baseline["configuration"][key], candidate["configuration"][key])
    for name, (before, after) in checks.items():
        if before != after:
            raise ValueError(f"non-comparable experiments: {name} differs")

    levels = {}
    full_token_coverage = True
    for level in ("1", "2", "4"):
        a, b = baseline["summary"][level], candidate["summary"][level]
        if a["failed_requests"] or b["failed_requests"]:
            raise ValueError("unsuccessful requests cannot be excluded from comparisons")
        measures = {
            "requests_per_second": "higher",
            "output_tokens_per_second": "higher",
            "latency_run_p50_ms": "lower",
            "latency_run_p95_ms": "lower",
            "ttft_run_p50_ms": "lower",
            "ttft_run_p95_ms": "lower",
        }
        compared = {}
        for name, direction in measures.items():
            aa, bb = a.get(name), b.get(name)
            if aa is None or bb is None:
                compared[name] = {"baseline_median": None, "candidate_median": None,
                                  "percentage_change": None, "prefer": direction}
                if name == "output_tokens_per_second":
                    full_token_coverage = False
                elif name.endswith("p95_ms"):
                    raise ValueError("missing per-run p95 evidence; rerun the updated sweep")
                continue
            before, after = aa["median"], bb["median"]
            compared[name] = {
                "baseline_median": before, "candidate_median": after,
                "percentage_change": _percent_change(before, after),
                "prefer": direction,
            }
        levels[level] = compared

    quality_before, quality_after = baseline["quality"], candidate["quality"]
    quality_same_size = baseline["quality_samples"] == candidate["quality_samples"]
    if not quality_same_size:
        raise ValueError("quality sample counts differ")
    quality_regression = any(
        quality_after[k] + 1e-9 < quality_before[k]
        for k in ("exact_match", "normalized_exact_match")
    )
    adequate_samples = (
        baseline["quality_samples"] >= min_quality_samples
        and baseline["configuration"]["requests_per_run"] >= min_requests_per_run
    )
    if quality_regression:
        interpretation = "quality_regression_not_eligible"
    elif not adequate_samples:
        interpretation = "exploratory_small_sample"
    elif not full_token_coverage:
        interpretation = "descriptive_missing_token_usage"
    else:
        interpretation = "descriptive_not_causal"
    return {
        "schema_version": 1,
        "method": "matched_local_runs; run_level_medians; no_pooled_percentiles",
        "change_description_self_reported": change.strip(),
        "model_digest": baseline["manifest"]["model_digest"],
        "benchmark_dataset_sha256": baseline["environment"]["dataset_sha256"],
        "quality_dataset_sha256": baseline["quality_dataset_sha256"],
        "repeats": baseline["configuration"]["repeats"],
        "requests_per_run": baseline["configuration"]["requests_per_run"],
        "quality": {
            "samples": baseline["quality_samples"], "baseline": quality_before,
            "candidate": quality_after, "non_regression": not quality_regression,
        },
        "minimums": {"quality_samples": min_quality_samples,
                     "requests_per_run": min_requests_per_run},
        "comparison_status": interpretation,
        "metrics_by_concurrency": levels,
        "limitations": [
            "This is descriptive; changes are self-reported, not independently controlled or causal",
            "Evidence manifests are human-attested; reports and hardware are not cryptographically verified",
            "Comparison excludes failed runs; failing raw evidence causes the comparison to abort",
            "Changes to model digest, quantization, runtime, hardware, workload or generation cap require a separate protocol",
            "Run-level median deltas are not confidence intervals or pooled request percentiles",
            "Exact/normalized match are not semantic, safety, or factuality evaluation",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two private, fully checked local evidence bundles")
    parser.add_argument("--baseline", type=Path, required=True,
                        help="Baseline evidence-manifest.json")
    parser.add_argument("--candidate", type=Path, required=True,
                        help="Candidate evidence-manifest.json")
    parser.add_argument("--change", required=True, help="Brief operator-declared serving setting change")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-quality-samples", type=int, default=30)
    parser.add_argument("--min-requests-per-run", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        target = args.output.resolve()
        if (target in (args.baseline.resolve(), args.candidate.resolve())
                or target.exists() or target.suffix != ".json"):
            raise ValueError("output must be a new, separate JSON report")
        result = compare(args.baseline, args.candidate, args.change,
                         min_quality_samples=args.min_quality_samples,
                         min_requests_per_run=args.min_requests_per_run)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2, sort_keys=True)
            output.write("\n")
        print(f"Descriptive comparison created: {target}")
        print(f"Interpretation: {result['comparison_status']} (not a causal speedup claim)")
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        # Never print raw reports or operator-supplied notes/URLs.
        print(f"comparison rejected: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
