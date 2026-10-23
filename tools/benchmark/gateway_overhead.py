"""Operator-only paired localhost Ollama-vs-Go serving-path diagnostic.

This isolates *total client-observed path differences*, NOT an optimized
Metriqen algorithm or a causal speedup. Default is offline plan-only.
Runs only with --execute on macOS, pinned installed model and AC power.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

from tools.benchmark import runner

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "local"
DATASET = ROOT / "docs" / "benchmarks" / "workload.v1.jsonl"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
GATEWAY_HEALTH = "http://127.0.0.1:8081/health/upstream"
ENDPOINTS = {
    "native": "http://127.0.0.1:11434/v1/chat/completions",
    "gateway": "http://127.0.0.1:8081/v1/chat/completions",
}
CONCURRENCY = (1, 2, 4)
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def plan() -> tuple[tuple[int, int, str], ...]:
    """Predeclared ABBA pairs; each concurrency independently alternates."""
    return tuple(
        (c, phase, name)
        for c in CONCURRENCY
        for phase, name in enumerate(("native", "gateway", "gateway", "native"), 1)
    )


def config_validation(model: str, digest: str, requests: int, tokens: int,
                      output_dir: Path) -> Path:
    if not MODEL.fullmatch(model):
        raise ValueError("invalid model tag")
    if not DIGEST.fullmatch(digest):
        raise ValueError("exact lowercase model digest required")
    if type(requests) is not int or not 4 <= requests <= 36:
        raise ValueError("requests per phase must be within 4..36")
    if type(tokens) is not int or not 1 <= tokens <= 128:
        raise ValueError("output cap must be within 1..128")
    # Refuse arbitrary paths, symlinks, non-ignored or existing destinations.
    if ".." in output_dir.parts:
        raise ValueError("path traversal is forbidden")
    target = output_dir.expanduser().absolute()
    base = REPORT_ROOT.absolute()
    # Resolve *parent* as well: no nested symlink may escape the ignored
    # private reports/local directory before the new path is created.
    if (target == base or not target.is_relative_to(base)
            or not target.parent.resolve().is_relative_to(base.resolve())):
        raise ValueError("output must be a fresh directory under reports/local")
    if output_dir.is_symlink() or output_dir.exists():
        raise ValueError("output directory must not exist")
    if base.is_symlink() or (base.exists() and base.stat().st_mode & 0o077):
        raise ValueError("reports/local must be a private 0700 directory")
    return target


def _probe_fixed_json(url: str) -> dict:
    if url not in (OLLAMA_TAGS, GATEWAY_HEALTH):
        raise ValueError("only allowlisted localhost read-only probes")
    opener = build_opener(ProxyHandler({}), _RejectRedirect())
    req = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with opener.open(req, timeout=4) as response:
            if response.geturl() != url or response.status != 200:
                raise ValueError("non-successful or redirected local preflight")
            body = response.read(64 * 1024 + 1)
            if len(body) > 64 * 1024:
                raise ValueError("local preflight response too large")
            result = json.loads(body)
    except (OSError, ValueError, json.JSONDecodeError):
        raise ValueError("local runtime preflight failed") from None
    if not isinstance(result, dict):
        raise ValueError("unexpected local runtime preflight data")
    return result


def preflight(model: str, expected_digest: str) -> None:
    if platform.system() != "Darwin":
        raise ValueError("real operator diagnostic is macOS-only")
    # Explicit stable plugged-in power prerequisite; no inference on battery.
    try:
        power = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                               text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("AC power verification unavailable") from None
    if "Now drawing from 'AC Power'" not in power.stdout:
        raise ValueError("AC power required; do not measure on battery")
    models = _probe_fixed_json(OLLAMA_TAGS).get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama installed-model report missing")
    digests = [
        m.get("digest") for m in models
        if isinstance(m, dict) and m.get("name") == model
    ]
    if digests != [expected_digest]:
        raise ValueError("installed model tag or digest differs from pinned value")
    reachability = _probe_fixed_json(GATEWAY_HEALTH)
    if reachability.get("status") != "reachable":
        raise ValueError("gateway upstream is not healthy")
    # Reachability does not prove gateway upstream == this local Ollama,
    # model loaded/resident or identical authorization and runtime controls.


def run_local(model: str, digest: str, requests: int, tokens: int,
              dest: Path) -> dict:
    """Fully opt-in: no retries, no output overwrite, no missing-run filtering."""
    validated = config_validation(model, digest, requests, tokens, dest)
    dataset, dataset_sha = runner.read_dataset(DATASET)
    preflight(model, digest)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(REPORT_ROOT, 0o700)
    validated.mkdir(mode=0o700, parents=False, exist_ok=False)
    results: list[dict] = []
    for c, phase, name in plan():
        preflight(model, digest)  # fail on mid-run changed model or battery
        endpoint = ENDPOINTS[name]
        # Reuse existing stream TTFT and per-request outcome counters.
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            observation = runner.run(
                endpoint, model, dataset, stream=True, max_tokens=tokens,
                concurrency=c, requests=requests, warmup=1, timeout=120.0,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError("local phase interrupted; private partial evidence retained") from None
        report = {
            "phase": phase, "concurrency": c, "route": name,
            "started_at_utc": started_at,
            "results": observation,
        }
        filepath = validated / f"c{c}-phase{phase}-{name}.json"
        with filepath.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(filepath, 0o600)
        results.append({
            "phase": phase, "concurrency": c, "route": name,
            "successes": observation["successes"],
            "failures": observation["failures"],
            "errors": observation["errors"],
            "requests_per_second": observation["requests_per_second"],
            "p95_latency_ms_successes_only": observation["latency_ms"]["p95"],
            "p95_ttft_ms_successes_only": (
                observation["ttft_ms"]["p95"]
                if observation["ttft_ms"] else None
            ),
            "completion_token_coverage": observation["completion_token_coverage"],
        })
        print(f"Completed c={c}, phase={phase}, route={name}: "
              f"{observation['successes']}/{requests} succeeded", flush=True)
    summary = {
        "schema": "metriqen.gateway-overhead-diagnostic.v1",
        "classification": "descriptive_local_path_diagnostic_not_speedup",
        "model": model, "expected_model_digest": digest,
        "dataset_sha256": dataset_sha,
        "configuration": {
            "concurrency": list(CONCURRENCY), "order_per_concurrency": [
                "native", "gateway", "gateway", "native",
            ],
            "measured_requests_per_phase": requests,
            "max_output_tokens": tokens, "warmup_per_phase": 1,
            "stream": True,
        },
        "results": results,
        "limitations": [
            "Operator-observed client timings, not authenticated Mac or GPU evidence",
            "Native Ollama and gateway paths may differ in admission, validation and instrumentation",
            "Gateway health alone does not attest its real upstream URL or loaded model",
            "Power checks sample boundaries only; true thermals and runtime slots need human review",
            "No causal Metriqen-controlled optimization; quality evaluation is separate",
            "No aggregate inference-speedup claim from this diagnostic",
        ],
        "causal_speedup_verified": False,
    }
    with (validated / "summary.json").open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(validated / "summary.json", 0o600)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or explicitly run localhost direct Ollama vs Go path diagnostics"
    )
    parser.add_argument("--execute", action="store_true",
                        help="send real model requests; absent means no network or files")
    parser.add_argument("--model", default="llama3.2:1b")
    parser.add_argument("--expected-digest", default="")
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--output-dir", type=Path,
                        default=REPORT_ROOT / "gateway-overhead-operator")
    args = parser.parse_args(argv)
    if not args.execute:
        print("PLAN ONLY: 12 ABBA phases at concurrency 1,2,4; no network/model calls.")
        print("Not an optimized-go-vs-baseline code comparison or P3 sign-off.")
        print("To run: provide --execute --expected-digest REAL_SHA256 "
              "and a fresh private --output-dir.")
        return 0
    try:
        result = run_local(args.model, args.expected_digest, args.requests,
                           args.max_tokens, args.output_dir)
        print("Private descriptive diagnostic saved. "
              "Causal optimization verified: false.")
        return 0 if len(result["results"]) == 12 else 2
    except (OSError, ValueError, RuntimeError):
        print("Local diagnostic blocked or incomplete; "
              "inspect private reports without claiming success.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
