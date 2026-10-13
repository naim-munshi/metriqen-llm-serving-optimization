"""Render an offline, non-causal A/B/A reviewer summary from validated private evidence.

This tool does not execute inference, verify operator phase settings or
hardware, copy prompts, or publish evidence to any remote service.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tools.benchmark import aba_analysis

TITLES = {
    "requests_per_second": ("Successful requests/sec", "requests/s"),
    "output_tokens_per_second": ("Output tokens/sec", "tokens/s"),
    "latency_run_p50_ms": ("Client latency p50", "ms"),
    "latency_run_p95_ms": ("Client latency p95", "ms"),
    "ttft_run_p50_ms": ("TTFT p50", "ms"),
    "ttft_run_p95_ms": ("TTFT p95", "ms"),
}


def _value(number: int | float | None) -> str:
    return "N/A" if number is None else f"{number:.3f}"


def _change(number: int | float | None) -> str:
    return "N/A" if number is None else f"{number:+.2f}%"


def render(result: dict) -> str:
    """Use only verified numeric summaries and fixed textual labels."""
    if (result.get("schema_version") != 1
            or result.get("kind") != "human_attested_local_aba_reversion"):
        raise ValueError("unexpected A/B/A analysis result")
    status = result["status"]
    if status not in {
        "descriptive_reversion_observations_only", "quality_regression_requires_review",
        "exploratory_small_sample", "descriptive_missing_token_usage",
    }:
        raise ValueError("unexpected analysis classification")

    metadata, quality = result["metadata"], result["quality"]
    sections = [
        "# Metriqen local A/B/A serving experiment — reviewer summary",
        "",
        "**Classification:** \`" + status + "\` — descriptive only, NOT proof of a causal speedup.",
        "",
        "Phases **A1 (parallelism 1) → B (parallelism 2) → A2 (parallelism 1)**",
        "are **operator-asserted**, not independently authenticated by this tool.",
        "The A1/B measurements originated from an earlier session; the later",
        "A2 does not turn this into a prospectively controlled or randomized study.",
        "",
        "## Experiment identity",
        "",
        "- Model digest: \`" + metadata["model_digest"] + "\`",
        "- Benchmark dataset SHA-256: \`" + metadata["benchmark_dataset_sha256"] + "\`",
        "- Quality dataset SHA-256: \`" + metadata["quality_dataset_sha256"] + "\`",
        "- Repetitions per concurrency per phase: " + str(metadata["repeats"]),
        "- Measured requests per run: " + str(metadata["requests_per_run"]),
        "",
        "## Per-concurrency results",
        "",
        "Each median is taken across repeated run summaries, NOT pooled requests.",
        "Percent changes are (later/earlier − 1) × 100.",
        "Higher throughput is preferable; lower latency and TTFT are preferable.",
        "The A1/A2 midpoint is context only, not a causal counterfactual.",
        "",
        "| Client concurrency | Metric | A1 median | B median | A2 median | A1→B | A1→A2 drift | B vs A midpoint |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    results = result["metrics_by_concurrency"]
    for concurrency in ("1", "2", "4"):
        for key in aba_analysis.METRICS:
            item = results[concurrency][key]
            title, unit = TITLES[key]
            sections.append(
                f"| {concurrency} | {title} ({unit}) | "
                f"{_value(item['A1_median'])} | {_value(item['B_median'])} | "
                f"{_value(item['A2_median'])} | "
                f"{_change(item['A1_to_B_percentage_change'])} | "
                f"{_change(item['A1_to_A2_drift_percentage_change'])} | "
                f"{_change(item['B_vs_A1_A2_center_percentage_change'])} |"
            )

    sections += [
        "",
        "## Narrow reference-quality check",
        "",
        "These are exact/normalized string matches, NOT semantic, factuality,",
        "hallucination or safety evaluation.",
        "",
        "| Phase | Exact match | Normalized exact match |",
        "| --- | ---: | ---: |",
    ]
    for phase in ("A1", "B", "A2"):
        scores = quality["scores"][phase]
        sections.append(
            f"| {phase} | {_change(scores['exact_match'] * 100).replace('+', '')} | "
            f"{_change(scores['normalized_exact_match'] * 100).replace('+', '')} |"
        )
    sections += [
        "",
        f"All scores use {quality['samples']} reference samples per phase.",
        "",
        "## Interpretation and evidence boundary",
        "",
        "- Compare B's direction/magnitude with the **A1→A2 same-setting drift**;",
        "  no stability cutoff was predeclared and no automatic recovery verdict exists.",
        "- Three repeats per concurrency cannot establish population p95 confidence intervals",
        "  or statistically significant/causal effects.",
        "- Real device, parallelism setting, timestamps, thermal/power/background",
        "  load and phase order require independent operator notes or corroboration.",
        "- The original A1/B observation and later A2 are **not a prospective",
        "  uninterrupted A/B/A design**; cache warming and time drift may differ.",
        "- Synthetic performance and narrow quality workloads are not representative",
        "  of production latency SLOs or broad language-model accuracy.",
        "- This report is generated from three locally checked, human-attested",
        "  evidence bundles. Public GitHub CI does **not** verify those private files.",
        "- Review locally before publishing. Do not publish raw responses, prompts,",
        "  private server logs or unsupported optimization claims.",
        "",
    ]
    if status == "quality_regression_requires_review":
        sections += [
            "**Quality warning:** at least one phase has an observed exact/normalized",
            "reference-score regression against A1. Do not present it as quality parity.",
            "",
        ]
    if status == "exploratory_small_sample":
        sections += [
            "**Sample warning:** configured sample counts are below the comparator's",
            "descriptive threshold; results are exploratory only.",
            "",
        ]
    if status == "descriptive_missing_token_usage":
        sections += [
            "**Usage warning:** output-token throughput cannot be inferred when",
            "token usage coverage is incomplete.",
            "",
        ]
    return "\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revalidate three private evidence manifests and write a local A/B/A Markdown review"
    )
    parser.add_argument("--a1", required=True, type=Path)
    parser.add_argument("--b", required=True, type=Path)
    parser.add_argument("--a2", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="New Markdown filename, ideally under reports/local/")
    args = parser.parse_args(argv)
    try:
        destination = args.output.resolve()
        if (destination.suffix != ".md" or destination.exists()
                or destination in {args.a1.resolve(), args.b.resolve(), args.a2.resolve()}):
            raise ValueError("report must be a new separate Markdown file")
        result = aba_analysis.analyze(args.a1, args.b, args.a2)
        markdown = render(result)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as file:
            file.write(markdown)
        print(f"Checked local A/B/A Markdown review created: {destination}")
        print("Descriptive only; no hardware attestation, verified slots or causal claims.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        # Do not include operator paths, private reports or notes in stderr.
        print("A/B/A review rejected: invalid, inconsistent or incomplete private evidence.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
