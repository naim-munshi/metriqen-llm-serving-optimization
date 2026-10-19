"""Strict offline review of two *real, operator-sourced* local FIFO overload runs.

P3 experimental aid: counterfactual interpretation is NOT guaranteed, and
neither benchmark CI nor these checks establish an end-to-end speedup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

from tools.benchmark import admission_load, phase_run

SHA = re.compile(r"[0-9a-f]{64}\Z")
LEVELS = ("4", "6", "8", "12")
MAX_BYTES = 4 * 1024 * 1024


class ReviewError(ValueError):
    """Only bounded codes; never reproduce private model output."""


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ReviewError("duplicate_json_keys")
        value[key] = item
    return value


def _bad_number(value):
    raise ReviewError("nonfinite_json")


def _read(root: Path, filename: str) -> dict:
    path = root / filename
    resolved = path.resolve()
    if (path.is_symlink() or not resolved.is_relative_to(root.resolve()) or
            not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES):
        raise ReviewError("unsafe_missing_or_oversized_report")
    try:
        record = json.loads(path.read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_keys, parse_constant=_bad_number)
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, ReviewError):
            raise
        raise ReviewError("invalid_report_json") from None
    if not isinstance(record, dict):
        raise ReviewError("report_must_be_object")
    return record


def _when(value) -> datetime:
    if not isinstance(value, str):
        raise ReviewError("missing_timestamp")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReviewError("invalid_timestamp") from None
    if moment.tzinfo is None or moment.utcoffset() != timezone.utc.utcoffset(moment):
        raise ReviewError("timestamp_must_be_utc")
    return moment


def _phase(path: Path, depth: int) -> tuple[dict, datetime, datetime]:
    if (path.is_symlink() or not path.is_dir() or
            not path.resolve().is_relative_to(phase_run.PRIVATE.resolve())):
        raise ReviewError("phase_must_be_private_directory")
    report = _read(path, "admission-summary.json")
    config = report.get("configuration")
    env = report.get("environment")
    entries = report.get("run_files")
    if (report.get("schema_version") != 1 or
            report.get("kind") != "local_admission_overload_observation" or
            report.get("model_inference_attested") is not False or
            report.get("performance_improvement_proven") is not False or
            not isinstance(config, dict) or not isinstance(env, dict) or
            not isinstance(entries, list) or
            config.get("expected_queue_depth") != depth or
            config.get("concurrency_levels") != [4, 6, 8, 12] or
            env.get("model") != "llama3.2:1b" or
            env.get("endpoint") != "http://127.0.0.1:8081/v1/chat/completions" or
            not isinstance(env.get("model_revision_self_reported"), str) or
            not SHA.fullmatch(env["model_revision_self_reported"]) or
            not isinstance(env.get("dataset_sha256"), str) or
            not SHA.fullmatch(env["dataset_sha256"])):
        raise ReviewError("invalid_overload_summary_profile")
    repeats, requests = config.get("repeats"), config.get("requests_per_run")
    if (type(repeats) is not int or not 3 <= repeats <= 10 or
            type(requests) is not int or requests < 30 or
            len(entries) != 4 * repeats):
        raise ReviewError("incomplete_or_undersampled_overload_phase")
    expected = admission_load.run_order(repeats)
    records = []
    previous = None
    begin = None
    for index, (entry, (repeat, level)) in enumerate(zip(entries, expected)):
        name = f"repeat-{repeat:02d}-c{level}.json"
        if (not isinstance(entry, dict) or entry.get("file") != name or
                entry.get("repeat") != repeat or entry.get("concurrency") != level):
            raise ReviewError("run_identity_or_chronology_mismatch")
        row = _read(path, name)
        if (row.get("schema_version") != 1 or row.get("repeat") != repeat or
                row.get("concurrency") != level):
            raise ReviewError("raw_run_identity_mismatch")
        for key in ("started_at_utc", "finished_at_utc", "http_429_observed",
                    "admission_deltas_including_warmup"):
            if row.get(key) != entry.get(key):
                raise ReviewError("raw_run_summary_mismatch")
        start, finish = _when(row.get("started_at_utc")), _when(row.get("finished_at_utc"))
        if start >= finish or previous is not None and start < previous:
            raise ReviewError("run_timing_overlap")
        if begin is None:
            begin = start
        previous = finish
        results = row.get("results")
        expected_results = entry.get("results")
        if not isinstance(results, dict) or not isinstance(expected_results, dict):
            raise ReviewError("missing_run_results")
        if any(results.get(key) != value for key, value in expected_results.items()):
            raise ReviewError("raw_run_metric_mismatch")
        obs = results.get("observations")
        successes, failures, total = (results.get(k) for k in ("successes", "failures", "requests"))
        if (not isinstance(obs, list) or len(obs) != requests or
                type(total) is not int or total != requests or
                type(successes) is not int or type(failures) is not int or
                successes + failures != requests or
                sum(isinstance(x, dict) and x.get("success") is True for x in obs) != successes or
                sum(isinstance(x, dict) and x.get("status") == 429 for x in obs) != row.get("http_429_observed")):
            raise ReviewError("inconsistent_overload_request_outcomes")
        counts = row.get("admission_deltas_including_warmup")
        if (not isinstance(counts, dict) or
                set(counts) != set(admission_load.COUNTERS) or
                any(type(v) is not int or v < 0 for v in counts.values())):
            raise ReviewError("invalid_gateway_admission_counters")
        records.append(row)
    try:
        recomputed = admission_load.summary(records)
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        raise ReviewError("invalid_aggregate_metrics") from None
    if report.get("summary") != recomputed:
        raise ReviewError("raw_and_aggregate_metrics_disagree")
    return report, begin, previous


def _percent(base, candidate):
    if (base is None or candidate is None or
            type(base) not in (int, float) or type(candidate) not in (int, float) or
            not math.isfinite(base) or not math.isfinite(candidate) or base <= 0):
        return None
    return round(100 * (candidate - base) / base, 3)


def analyze(baseline_path: Path, candidate_path: Path) -> dict:
    if baseline_path.resolve() == candidate_path.resolve():
        raise ReviewError("baseline_candidate_must_be_distinct")
    baseline, a_start, a_end = _phase(baseline_path, 0)
    candidate, b_start, b_end = _phase(candidate_path, 2)
    if b_start < a_end:
        raise ReviewError("baseline_candidate_chronology_not_sequential")
    ea, eb = baseline["environment"], candidate["environment"]
    if ea != eb:
        raise ReviewError("environment_or_dataset_mismatch")
    ca, cb = dict(baseline["configuration"]), dict(candidate["configuration"])
    ca.pop("expected_queue_depth")
    cb.pop("expected_queue_depth")
    if ca != cb:
        raise ReviewError("multiple_experimental_configuration_changes")
    values = {}
    flags = []
    for level in LEVELS:
        a, b = baseline["summary"][level], candidate["summary"][level]
        sr = round(b["success_rate"] - a["success_rate"], 4)
        gp = _percent(a["goodput_rps_median"], b["goodput_rps_median"])
        lat = _percent(a["successful_latency_p95_ms_run_median"],
                       b["successful_latency_p95_ms_run_median"])
        ttft = _percent(a["successful_ttft_p95_ms_run_median"],
                        b["successful_ttft_p95_ms_run_median"])
        if sr < 0:
            flags.append(f"c{level}_success_rate_lower")
        if gp is not None and gp < 0:
            flags.append(f"c{level}_goodput_lower")
        if lat is not None and lat > 0:
            flags.append(f"c{level}_success_only_p95_latency_higher")
        if ttft is not None and ttft > 0:
            flags.append(f"c{level}_success_only_p95_ttft_higher")
        values[level] = {
            "baseline_successes": a["successes"],
            "candidate_successes": b["successes"],
            "baseline_http_429": a["http_429_observed"],
            "candidate_http_429": b["http_429_observed"],
            "success_rate_delta_fraction": sr,
            "goodput_rps_change_pct": gp,
            "successful_latency_p95_change_pct": lat,
            "successful_ttft_p95_change_pct": ttft,
        }
    return {
        "schema_version": 1,
        "kind": "operator_sourced_metriqen_fifo_overload_descriptive_review",
        "classification": "descriptive_tradeoffs_not_causal_or_attested",
        "model": ea["model"],
        "pinned_digest_prefix": ea["model_revision_self_reported"][:12],
        "baseline_queue_depth": 0,
        "candidate_queue_depth": 2,
        "observed_levels": values,
        "adverse_flags": flags,
        "causal_speedup_verified": False,
        "model_quality_verified": False,
        "portfolio_p3_complete": False,
        "limitations": [
            "Both phase identities and actual Mac/Ollama runtime slots remain operator-sourced",
            "HTTP 429 may originate from gateway or upstream; inspect admission counter deltas and raw logs",
            "Only successful-request p95 is shown; failed requests remain counted in success and 429 totals",
            "Queueing can improve acceptance while increasing latency; no single-number optimization claim",
            "Different elapsed times, thermal load and gateway restart confound causal inference",
            "Separate model reference quality checks and owner-reviewed P3 attribution required",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review two private FIFO overload run directories")
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        base = phase_run.PRIVATE.resolve()
        dest = args.output.resolve()
        if (args.output.suffix != ".json" or not dest.is_relative_to(base) or
                dest == args.baseline_dir.resolve() or dest == args.candidate_dir.resolve()):
            raise ReviewError("output_must_be_private_json")
        result = analyze(args.baseline_dir, args.candidate_dir)
        args.output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(args.output, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
        print("P3 FIFO overload private tradeoff review: PASS (descriptive only)")
        print("All rejected requests counted. No causal speedup, model quality, or P3 acceptance attested.")
        return 0
    except (ReviewError, OSError, ValueError, KeyError, TypeError):
        print("P3 overload review blocked: invalid or inconsistent private operator evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
