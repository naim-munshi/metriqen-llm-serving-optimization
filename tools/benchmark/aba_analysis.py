"""Offline descriptive A/B/A reversion analysis of three evidence bundles.

A1 (parallelism 1) -> B (parallelism 2) -> A2 (parallelism 1).
Each pair is revalidated by the existing strict comparator. This does not
verify active slots, hardware authenticity, timing, or causal attribution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tools.benchmark import compare

METRICS = (
    "requests_per_second",
    "output_tokens_per_second",
    "latency_run_p50_ms",
    "latency_run_p95_ms",
    "ttft_run_p50_ms",
    "ttft_run_p95_ms",
)


def analyze(a1: Path, b: Path, a2: Path) -> dict:
    """Return only aggregate observations; never write raw prompts or answers."""
    resolved = (a1.resolve(), b.resolve(), a2.resolve())
    if len(set(resolved)) != 3:
        raise ValueError("A1, B and A2 must be three distinct evidence manifests")

    # All three calls read the raw runs and independently verify each manifest,
    # sweep summary, observations, dataset and reference quality evidence.
    ab = compare.compare(a1, b, "A1 parallelism 1 to B parallelism 2")
    ba2 = compare.compare(b, a2, "B parallelism 2 to A2 parallelism 1")
    aa2 = compare.compare(a1, a2, "A1 to A2 same-setting drift")

    identities = ("model_digest", "benchmark_dataset_sha256", "quality_dataset_sha256",
                  "repeats", "requests_per_run")
    for key in identities:
        if ab[key] != ba2[key] or ab[key] != aa2[key]:
            raise ValueError("all A/B/A phases must use the same pinned experiment profile")

    quality = {
        "A1": ab["quality"]["baseline"],
        "B": ab["quality"]["candidate"],
        "A2": aa2["quality"]["candidate"],
    }
    if ba2["quality"]["baseline"] != quality["B"] or ba2["quality"]["candidate"] != quality["A2"]:
        raise ValueError("quality results disagree across pairwise checks")
    for phase in ("B", "A2"):
        if any(
            quality[phase][key] + 1e-9 < quality["A1"][key]
            for key in ("exact_match", "normalized_exact_match")
        ):
            quality_regression = True
            break
    else:
        quality_regression = False

    metrics = {}
    usage_missing = False
    for level in ("1", "2", "4"):
        per_level = {}
        for metric in METRICS:
            first = ab["metrics_by_concurrency"][level][metric]
            return_phase = ba2["metrics_by_concurrency"][level][metric]
            drift = aa2["metrics_by_concurrency"][level][metric]
            if (first["baseline_median"] != drift["baseline_median"]
                    or first["candidate_median"] != return_phase["baseline_median"]
                    or drift["candidate_median"] != return_phase["candidate_median"]):
                raise ValueError("pairwise experiment aggregates disagree")
            values = (first["baseline_median"], first["candidate_median"], drift["candidate_median"])
            if any(v is None for v in values):
                if metric != "output_tokens_per_second":
                    raise ValueError("missing non-token metric")
                usage_missing = True
                center = None
            else:
                center = (values[0] + values[2]) / 2
            per_level[metric] = {
                "A1_median": values[0],
                "B_median": values[1],
                "A2_median": values[2],
                "A1_to_B_percentage_change": first["percentage_change"],
                "B_to_A2_percentage_change": return_phase["percentage_change"],
                "A1_to_A2_drift_percentage_change": drift["percentage_change"],
                "B_vs_A1_A2_center_percentage_change": (
                    compare._percent_change(center, values[1]) if center is not None else None
                ),
                "prefer": first["prefer"],
            }
        metrics[level] = per_level

    statuses = (ab["comparison_status"], ba2["comparison_status"], aa2["comparison_status"])
    if quality_regression:
        status = "quality_regression_requires_review"
    elif "exploratory_small_sample" in statuses:
        status = "exploratory_small_sample"
    elif usage_missing:
        status = "descriptive_missing_token_usage"
    else:
        status = "descriptive_reversion_observations_only"

    return {
        "schema_version": 1,
        "kind": "human_attested_local_aba_reversion",
        "phase_labels_operator_asserted_not_verified": {
            "A1": "parallelism=1", "B": "parallelism=2", "A2": "parallelism=1"
        },
        "metadata": {key: ab[key] for key in identities},
        "status": status,
        "quality": {
            "samples": ab["quality"]["samples"],
            "scores": quality,
            "B_no_observed_regression_from_A1": ab["quality"]["non_regression"],
            "A2_no_observed_regression_from_A1": aa2["quality"]["non_regression"],
        },
        "pairwise_statuses": {"A1_to_B": statuses[0], "B_to_A2": statuses[1],
                              "A1_to_A2": statuses[2]},
        "metrics_by_concurrency": metrics,
        "limitations": [
            "Operator phase labels and runtime slot counts are not authenticated by these JSON reports",
            "Old A1/B sessions combined with a later A2 are exploratory, not a prospective uninterrupted A/B/A",
            "Three per-run medians at each concurrency do not establish confidence intervals or causality",
            "A1-to-A2 drift has no predetermined stability threshold; no automatic pass/fail on recovery",
            "Center of two A medians is only a descriptive reference, not a causal counterfactual",
            "Cache, thermal, power, OS load and model lifetime may differ across phases",
            "Exact-match reference quality does not establish semantic or safety equivalence",
            "No hardware, GPU, active parallelism or real-model execution is independently attested",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze three operator-verified local A/B/A evidence bundles (offline)"
    )
    parser.add_argument("--a1", required=True, type=Path, help="First one-slot evidence manifest")
    parser.add_argument("--b", required=True, type=Path, help="Two-slot candidate evidence manifest")
    parser.add_argument("--a2", required=True, type=Path, help="Return-to-one-slot evidence manifest")
    parser.add_argument("--output", required=True, type=Path, help="New local aggregate JSON path")
    args = parser.parse_args(argv)
    try:
        target = args.output.resolve()
        if (target.suffix != ".json" or target.exists()
                or target in {args.a1.resolve(), args.b.resolve(), args.a2.resolve()}):
            raise ValueError("output must be a new, separate JSON file; never overwrite evidence")
        result = analyze(args.a1, args.b, args.a2)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as file:
            json.dump(result, file, indent=2, sort_keys=True)
            file.write("\n")
        print(f"Offline A/B/A descriptive report created: {target}")
        print(f"Status: {result['status']}; phase authenticity and causality NOT established.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"A/B/A analysis rejected: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
