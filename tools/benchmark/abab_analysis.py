"""Offline descriptive A/B/A/B replication review of four private evidence bundles.

Phase labels and serving configuration are supplied by the operator, not
cryptographically attested. All per-request raw reports and reference-quality
evidence are revalidated through the existing strict comparator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tools.benchmark import aba_analysis, compare, evidence_gate, run_timing

PHASES = ("A1", "B1", "A2", "B2")
PAIRS = (
    ("first_candidate", "A1", "B1"),
    ("replicated_candidate", "A2", "B2"),
    ("baseline_drift", "A1", "A2"),
    ("candidate_drift", "B1", "B2"),
)
PROFILE = ("model_digest", "benchmark_dataset_sha256",
           "quality_dataset_sha256", "repeats", "requests_per_run")


def _phase_timing(filename: Path) -> dict | None:
    """Read run chronology only after the comparator has verified this bundle."""
    manifest = filename.resolve()
    summary, _ = evidence_gate._load_local_report(manifest.parent, "sweep-summary.json")
    entries = summary["run_files"]
    run_timing.validate_sequence(entries)
    if not entries or "timing" not in entries[0]:
        return None
    return {
        "start_utc": entries[0]["timing"]["started_at_utc"],
        "end_utc": entries[-1]["timing"]["finished_at_utc"],
    }


def analyze(a1: Path, b1: Path, a2: Path, b2: Path) -> dict:
    """Return descriptive aggregates only; no raw messages or local paths."""
    paths = dict(zip(PHASES, (a1, b1, a2, b2)))
    if len({filename.resolve() for filename in paths.values()}) != len(PHASES):
        raise ValueError("A1, B1, A2 and B2 must be distinct evidence manifests")
    pair_results = {}
    for key, before, after in PAIRS:
        pair_results[key] = compare.compare(
            paths[before], paths[after],
            f"operator-asserted {before} to {after} local parallelism comparison",
        )
    first = pair_results["first_candidate"]
    for pair in pair_results.values():
        if any(pair[name] != first[name] for name in PROFILE):
            raise ValueError("all four phases must have one matched experiment profile")

    quality_scores = {
        "A1": first["quality"]["baseline"],
        "B1": first["quality"]["candidate"],
        "A2": pair_results["replicated_candidate"]["quality"]["baseline"],
        "B2": pair_results["replicated_candidate"]["quality"]["candidate"],
    }
    base_drift = pair_results["baseline_drift"]["quality"]
    candidate_drift = pair_results["candidate_drift"]["quality"]
    if (base_drift["baseline"] != quality_scores["A1"]
            or base_drift["candidate"] != quality_scores["A2"]
            or candidate_drift["baseline"] != quality_scores["B1"]
            or candidate_drift["candidate"] != quality_scores["B2"]):
        raise ValueError("quality results disagree across independently verified pairs")
    if any(pair["quality"]["samples"] != first["quality"]["samples"]
           for pair in pair_results.values()):
        raise ValueError("quality sample counts differ across four phases")

    phase_summaries = {
        phase: compare._read_experiment(filename)["summary"]
        for phase, filename in paths.items()
    }
    observations = {}
    missing_usage = False
    for concurrency in ("1", "2", "4"):
        metrics = {}
        for metric in aba_analysis.METRICS:
            first_pair = pair_results["first_candidate"]["metrics_by_concurrency"][concurrency][metric]
            replicated = pair_results["replicated_candidate"]["metrics_by_concurrency"][concurrency][metric]
            a_drift = pair_results["baseline_drift"]["metrics_by_concurrency"][concurrency][metric]
            b_drift = pair_results["candidate_drift"]["metrics_by_concurrency"][concurrency][metric]
            values = {
                "A1": first_pair["baseline_median"],
                "B1": first_pair["candidate_median"],
                "A2": replicated["baseline_median"],
                "B2": replicated["candidate_median"],
            }
            if (a_drift["baseline_median"] != values["A1"]
                    or a_drift["candidate_median"] != values["A2"]
                    or b_drift["baseline_median"] != values["B1"]
                    or b_drift["candidate_median"] != values["B2"]):
                raise ValueError("pairwise metric summaries disagree")
            if any(value is None for value in values.values()):
                if metric != "output_tokens_per_second" or not all(
                    value is None for value in values.values()
                ):
                    # Missing token coverage on any phase makes the four-way
                    # token comparison unavailable, never a synthetic zero.
                    if metric != "output_tokens_per_second":
                        raise ValueError("required metric unavailable")
                missing_usage = True
            ranges = {}
            for phase, expected_median in values.items():
                recorded = phase_summaries[phase][concurrency].get(metric)
                if (recorded is None) != (expected_median is None):
                    raise ValueError("run summaries disagree with pairwise metrics")
                if recorded is not None and recorded.get("median") != expected_median:
                    raise ValueError("run median does not agree with pairwise result")
                ranges[phase] = (
                    None if recorded is None
                    else {"min": recorded["min"], "max": recorded["max"]}
                )
            metrics[metric] = {
                "medians": values,
                "repeat_ranges": ranges,
                "first_candidate_change_pct": first_pair["percentage_change"],
                "replicated_candidate_change_pct": replicated["percentage_change"],
                "baseline_drift_pct": a_drift["percentage_change"],
                "candidate_drift_pct": b_drift["percentage_change"],
                "prefer": first_pair["prefer"],
            }
        observations[concurrency] = metrics

    clock = {phase: _phase_timing(filename) for phase, filename in paths.items()}
    prior = None
    for phase in PHASES:
        timing = clock[phase]
        if timing is not None:
            started = run_timing.parse_utc(timing["start_utc"])
            finished = run_timing.parse_utc(timing["end_utc"])
            if prior is not None and started < prior:
                raise ValueError("phase timestamps overlap or violate operator-declared order")
            prior = finished
        else:
            prior = None  # Cannot reconstruct chronology through a legacy phase.
    timed_count = sum(item is not None for item in clock.values())
    chronology = ("timestamps_available_all_four" if timed_count == 4 else
                  "timestamps_unavailable_legacy" if timed_count == 0 else
                  "timestamps_partial_no_full_order_verification")

    quality_regression = any(
        quality_scores[phase][key] + 1e-9 < quality_scores["A1"][key]
        for phase in ("B1", "A2", "B2")
        for key in ("exact_match", "normalized_exact_match")
    )
    statuses = {key: report["comparison_status"]
                for key, report in pair_results.items()}
    if quality_regression:
        status = "quality_regression_requires_review"
    elif "exploratory_small_sample" in statuses.values():
        status = "exploratory_small_sample"
    elif missing_usage:
        status = "descriptive_missing_token_usage"
    else:
        status = "descriptive_replication_only"

    return {
        "schema_version": 1,
        "kind": "operator_asserted_local_abab_replication",
        "status": status,
        "phase_order_operator_asserted": {
            "A1": "parallelism=1", "B1": "parallelism=2",
            "A2": "parallelism=1", "B2": "parallelism=2",
        },
        "profile": {key: first[key] for key in PROFILE},
        "chronology": {"classification": chronology, "phase_intervals": clock},
        "quality": {"samples_per_phase": first["quality"]["samples"],
                    "scores": quality_scores},
        "pair_statuses": statuses,
        "metrics_by_concurrency": observations,
        "limitations": [
            "Phase configuration and true parallel slots are operator-asserted, not authenticated",
            "Four phases and repeated run medians do not prove a causal optimization speedup",
            "Historical/legacy phases without timestamps have unknown chronological separation",
            "Recorded wall-clock timestamps do not prove real-world execution timing",
            "Power, cache, model residency, thermals and system load can vary within and between phases",
            "Minimum and maximum across repeated runs are not statistical confidence intervals",
            "Exact and normalized matching do not establish factual, semantic or safety equivalence",
            "Private local reports are not independently measured by GitHub CI",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revalidate four private A/B/A/B evidence bundles; descriptive only"
    )
    for name in ("a1", "b1", "a2", "b2"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inputs = (args.a1, args.b1, args.a2, args.b2)
        target = args.output.resolve()
        if target.suffix != ".json" or target in {p.resolve() for p in inputs}:
            raise ValueError("output must be separate JSON")
        result = analyze(*inputs)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"Descriptive A/B/A/B analysis saved: {target}")
        print(f"Classification: {result['status']}; NOT causal or hardware-attested")
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("A/B/A/B analysis rejected: incomplete or inconsistent private evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
