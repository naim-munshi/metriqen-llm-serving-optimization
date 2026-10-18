"""Conservative, offline performance-tradeoff signal from VERIFIED raw sweeps.

No model execution, causal attribution, hardware attestations or production
SLO claims. A good signal can support, never complete, Portfolio P3.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from tools.benchmark import compare

LEVELS = ("1", "2", "4")
MEASURES = ("requests_per_second", "latency_run_p95_ms", "ttft_run_p95_ms")


def _percent(entry: dict, name: str) -> float | None:
    value = entry[name]["percentage_change"]
    if value is None:
        return None
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("invalid metric percentage delta")
    return float(value)


def review(baseline: Path, candidate: Path, change: str, *,
           target: int = 2, min_rps_gain: float = 5.0,
           max_regression: float = 5.0,
           min_quality_samples: int = 30,
           min_requests_per_run: int = 30) -> dict:
    if type(target) is not int or str(target) not in LEVELS:
        raise ValueError("target concurrency must be 1, 2 or 4")
    if (type(min_rps_gain) not in (int, float)
            or not math.isfinite(min_rps_gain) or not 0 < min_rps_gain <= 100
            or type(max_regression) not in (int, float)
            or not math.isfinite(max_regression) or not 0 <= max_regression <= 100):
        raise ValueError("invalid improvement or regression thresholds")
    result = compare.compare(
        baseline, candidate, change,
        min_quality_samples=min_quality_samples,
        min_requests_per_run=min_requests_per_run,
    )
    all_deltas = {}
    adverse = []
    for level in LEVELS:
        raw = result["metrics_by_concurrency"][level]
        values = {name: _percent(raw, name) for name in MEASURES}
        all_deltas[level] = values
        if values["requests_per_second"] is None:
            adverse.append(f"c{level}_rps_missing")
        elif values["requests_per_second"] < -max_regression:
            adverse.append(f"c{level}_rps_regression")
        for name in ("latency_run_p95_ms", "ttft_run_p95_ms"):
            if values[name] is None:
                adverse.append(f"c{level}_{name}_missing")
            elif values[name] > max_regression:
                adverse.append(f"c{level}_{name}_regression")
    target_gain = all_deltas[str(target)]["requests_per_second"]
    if result["comparison_status"] != "descriptive_not_causal":
        classification = "insufficient_or_ineligible_evidence"
    elif adverse:
        classification = "candidate_has_material_tradeoffs"
    elif target_gain is None or target_gain < min_rps_gain:
        classification = "target_gain_not_demonstrated"
    else:
        classification = "descriptive_guardrails_met_not_causal"
    return {
        "schema_version": 1,
        "kind": "operator_sourced_descriptive_optimization_review",
        "classification": classification,
        "comparison_status": result["comparison_status"],
        "target_concurrency": target,
        "minimum_target_rps_gain_pct": float(min_rps_gain),
        "maximum_other_regression_pct": float(max_regression),
        "metric_delta_pct_by_concurrency": all_deltas,
        "quality_non_regression": result["quality"]["non_regression"],
        "raw_evidence_eligible": result["comparison_status"] == "descriptive_not_causal",
        "tradeoff_flags": adverse,
        "causality_verified": False,
        "gpu_or_runtime_verified": False,
        "notes": [
            "Runs and changed code/configuration are operator sourced and not independently authenticated",
            "Includes all 1, 2 and 4 concurrency groups; any adverse metric is explicitly visible",
            "Result does not include memory, power, statistically robust uncertainty or semantic model quality",
            "Even when descriptive guardrails pass, the operator must inspect raw results and attribution",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Screen conservative optimizer tradeoffs; never attest speedups")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--change", required=True)
    p.add_argument("--target-concurrency", type=int, choices=(1, 2, 4), default=2)
    p.add_argument("--min-rps-gain", type=float, default=5.0)
    p.add_argument("--max-regression", type=float, default=5.0)
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    try:
        result = review(args.baseline, args.candidate, args.change,
                        target=args.target_concurrency, min_rps_gain=args.min_rps_gain,
                        max_regression=args.max_regression)
        if args.output is not None:
            if args.output.suffix != ".json" or args.output.resolve() in (
                args.baseline.resolve(), args.candidate.resolve()
            ):
                raise ValueError("output must be a separate JSON report")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as out:
                json.dump(result, out, indent=2, sort_keys=True)
                out.write("\n")
        print("Portfolio optimization review: " + result["classification"])
        print("No real hardware, causal effect, performance improvement or P3 completion attested.")
        return 0 if result["classification"] == "descriptive_guardrails_met_not_causal" else 2
    except (OSError, ValueError, TypeError, KeyError):
        print("Portfolio optimization review blocked: invalid or ineligible local evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
