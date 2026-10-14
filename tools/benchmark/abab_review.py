"""Sanitized local Markdown review of four verified private A/B/A/B manifests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tools.benchmark import aba_analysis, aba_review, abab_analysis

STATUSES = {
    "quality_regression_requires_review", "exploratory_small_sample",
    "descriptive_missing_token_usage", "descriptive_replication_only",
}


def _value(value):
    return "N/A" if value is None else f"{value:.3f}"


def _percent(value):
    return "N/A" if value is None else f"{value:+.2f}%"


def _range(value):
    return "N/A" if value is None else f"{_value(value['min'])}–{_value(value['max'])}"


def render(result: dict) -> str:
    if (result.get("schema_version") != 1
            or result.get("kind") != "operator_asserted_local_abab_replication"
            or result.get("status") not in STATUSES):
        raise ValueError("invalid four-phase analysis")
    profile = result["profile"]
    lines = [
        "# Metriqen A/B/A/B local replication — reviewer report", "",
        "**Classification:** " + result["status"] + " — descriptive only, NOT causal.",
        "Operator-asserted phases: A1 (parallelism 1), B1 (2), A2 (1), B2 (2).",
        "Phase settings and actual active slots are not authenticated.", "",
        "## Matched experiment identity", "",
        "- Model digest: " + profile["model_digest"],
        "- Performance dataset SHA-256: " + profile["benchmark_dataset_sha256"],
        "- Quality dataset SHA-256: " + profile["quality_dataset_sha256"],
        "- Repeats per concurrency per phase: " + str(profile["repeats"]),
        "- Measured requests per run: " + str(profile["requests_per_run"]), "",
        "## Per-concurrency performance, replication and drift", "",
        "Metrics are medians of repeated RUN summaries, not pooled request percentiles.",
        "Percentages are (later / earlier - 1) * 100; direction alone does not",
        "prove improvement. Higher throughput is preferable; lower latency/TTFT is.",
        "| C | Metric | A1 | B1 | A2 | B2 | A1→B1 | A2→B2 | A1→A2 drift | B1→B2 drift |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in ("1", "2", "4"):
        for metric in aba_analysis.METRICS:
            item = result["metrics_by_concurrency"][c][metric]
            label, unit = aba_review.TITLES[metric]
            vals = item["medians"]
            lines.append(
                f"| {c} | {label} ({unit}) | "
                + " | ".join(_value(vals[p]) for p in abab_analysis.PHASES)
                + f" | {_percent(item['first_candidate_change_pct'])}"
                + f" | {_percent(item['replicated_candidate_change_pct'])}"
                + f" | {_percent(item['baseline_drift_pct'])}"
                + f" | {_percent(item['candidate_drift_pct'])} |"
            )
    lines.extend(["", "## Repeat-run variability", "",
                  "Minimum–maximum across run summaries only; not a confidence interval.",
                  "| C | Metric | A1 repeat range | B1 repeat range | A2 repeat range | B2 repeat range |",
                  "| ---: | --- | ---: | ---: | ---: | ---: |"])
    for c in ("1", "2", "4"):
        for metric in aba_analysis.METRICS:
            item = result["metrics_by_concurrency"][c][metric]
            label, unit = aba_review.TITLES[metric]
            lines.append(
                f"| {c} | {label} ({unit}) | "
                + " | ".join(_range(item["repeat_ranges"][p]) for p in abab_analysis.PHASES)
                + " |"
            )
    quality = result["quality"]
    lines.extend(["", "## Narrow reference-quality checks", "",
                  "Exact/normalized string match only, not semantic, factuality or safety evaluation.",
                  "| Phase | Exact match | Normalized match |",
                  "| --- | ---: | ---: |"])
    for phase in abab_analysis.PHASES:
        score = quality["scores"][phase]
        lines.append(
            f"| {phase} | {score['exact_match']:.2%} | "
            f"{score['normalized_exact_match']:.2%} |"
        )
    chronology = result["chronology"]
    lines.extend([f"{quality['samples_per_phase']} reference samples per phase.", "",
                  "## Operator clock observations", "",
                  "Chronology classification: " + chronology["classification"],
                  "| Phase | First run start (UTC) | Last run end (UTC) |",
                  "| --- | --- | --- |"])
    for phase in abab_analysis.PHASES:
        interval = chronology["phase_intervals"][phase]
        lines.append(
            f"| {phase} | {interval['start_utc'] if interval else 'Not recorded'} "
            f"| {interval['end_utc'] if interval else 'Not recorded'} |"
        )
    lines.extend(["", "## Evidence limitations", "",
                  "- Compare BOTH candidate effects against baseline and candidate same-setting drift.",
                  "- Legacy timestamps cannot be inferred or backfilled.",
                  "- UTC timestamps are local observations, not trusted attestations.",
                  "- Sample minima, run ranges and percentage changes are not significance tests.",
                  "- Device, slots, power, cache, temperature and background load need operator notes.",
                  "- Public GitHub CI checks synthetic fixture data, not private physical inference.",
                  "- Raw prompt/response content and private paths are not included.", ""])
    if result["status"] == "quality_regression_requires_review":
        lines.extend(["**Quality warning:** at least one later phase has a worse",
                      "exact/normalized match rate than A1; parity is not established.", ""])
    if result["status"] == "exploratory_small_sample":
        lines.extend(["**Sample warning:** at least one comparison is below",
                      "minimum descriptive sample thresholds.", ""])
    if result["status"] == "descriptive_missing_token_usage":
        lines.extend(["**Usage warning:** output token throughput has incomplete",
                      "usage coverage in at least one phase.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review four fully checked local evidence bundles offline"
    )
    for name in ("a1", "b1", "a2", "b2"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        paths = (args.a1, args.b1, args.a2, args.b2)
        destination = args.output.resolve()
        if destination.suffix != ".md" or destination in {p.resolve() for p in paths}:
            raise ValueError("output must be a separate Markdown file")
        report = render(abab_analysis.analyze(*paths))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(report)
        print(f"Offline A/B/A/B review saved: {destination}")
        print("No authenticated active slots or causal performance attribution.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("A/B/A/B review rejected: invalid, incomplete or inconsistent private evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
