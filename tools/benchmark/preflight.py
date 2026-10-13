"""Read-only macOS preflight for local, developer-operated Ollama experiments.

No model inference, process restart or hardware attestation occurs here.
Only fixed loopback endpoints are queried and no shell is executed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Callable
from urllib.request import ProxyHandler, Request, build_opener

from tools.benchmark.runner import NoRedirect

MODEL_API = "http://127.0.0.1:11434"
GATEWAY_API = "http://127.0.0.1:8081"
HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ENV_PARALLEL = re.compile(r"(?:^|\s)OLLAMA_NUM_PARALLEL=(\d+)(?=\s|$)")
MEMORY_FREE = re.compile(r"System-wide memory free percentage:\s*(\d+)%")


def _command(argv: list[str]) -> str | None:
    """Return stdout only on success; never store stderr or full process env."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _get_json(url: str) -> dict | None:
    """Read bounded JSON from fixed local endpoints with no proxy/redirects."""
    if url not in (MODEL_API + "/api/version", MODEL_API + "/api/tags",
                   GATEWAY_API + "/health/live"):
        raise ValueError("unknown local preflight endpoint")
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(Request(url, headers={"Accept": "application/json"}),
                         timeout=3) as response:
            if response.headers.get_content_type() != "application/json":
                return None
            raw = response.read(65537)
            if len(raw) > 65536:
                return None
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def collect(*, expected_model_digest: str, expected_parallelism: int,
            model: str = "llama3.2:1b",
            command_fn: Callable[[list[str]], str | None] | None = None,
            json_fn: Callable[[str], dict | None] | None = None,
            system: str | None = None,
            timestamp_utc: str | None = None) -> dict:
    """Collect bounded diagnostics. Caller must verify actual runtime slots."""
    if HEX_DIGEST.fullmatch(expected_model_digest) is None:
        raise ValueError("expected model digest must be 64 lowercase hex characters")
    if type(expected_parallelism) is not int or expected_parallelism not in (1, 2):
        raise ValueError("expected parallelism must be 1 or 2")
    if model != "llama3.2:1b":
        raise ValueError("this preflight currently supports llama3.2:1b only")

    command_fn = command_fn if command_fn is not None else _command
    json_fn = json_fn if json_fn is not None else _get_json
    system = system if system is not None else platform.system()
    timestamp = timestamp_utc or datetime.now(timezone.utc).isoformat(timespec="seconds")

    problems = []
    if system != "Darwin":
        problems.append("This macOS-only preflight has not verified the host OS")

    battery_text = command_fn(["pmset", "-g", "batt"])
    if battery_text and "Now drawing from 'AC Power'" in battery_text:
        power_source = "AC"
    elif battery_text and "Now drawing from 'Battery Power'" in battery_text:
        power_source = "Battery"
    else:
        power_source = "unknown"
    if power_source != "AC":
        problems.append("AC power is not confirmed; do not begin the benchmark")

    version = json_fn(MODEL_API + "/api/version")
    version_text = version.get("version") if isinstance(version, dict) else None
    if not isinstance(version_text, str) or not version_text:
        version_text = None
        problems.append("Ollama API version endpoint did not return a version")

    tags = json_fn(MODEL_API + "/api/tags")
    found_digest = None
    if isinstance(tags, dict) and isinstance(tags.get("models"), list):
        matches = [item.get("digest") for item in tags["models"]
                   if isinstance(item, dict) and item.get("name") == model]
        if len(matches) == 1 and isinstance(matches[0], str):
            found_digest = matches[0]
    if found_digest != expected_model_digest:
        problems.append("Installed model digest does not match the pinned expected digest")

    health = json_fn(GATEWAY_API + "/health/live")
    gateway_live = (isinstance(health, dict)
                    and health.get("service") == "metriqen-inference-gateway"
                    and health.get("status") == "ok")
    if not gateway_live:
        problems.append("Metriqen gateway health endpoint did not confirm liveness")

    pgrep = command_fn(["pgrep", "-fl", "ollama serve"])
    pids = []
    if pgrep:
        for line in pgrep.splitlines():
            match = re.fullmatch(r"\s*(\d+)\s+(.+)", line)
            if match and match.group(2).endswith("ollama serve"):
                pids.append(int(match.group(1)))
    if len(pids) != 1:
        problems.append("Exactly one Ollama serve process was not confirmed")

    configured_parallelism = None
    if len(pids) == 1:
        process_text = command_fn(["ps", "eww", "-p", str(pids[0])])
        env = ENV_PARALLEL.search(process_text or "")
        if env:
            configured_parallelism = int(env.group(1))
    if configured_parallelism != expected_parallelism:
        problems.append("Ollama process parallelism environment is absent or mismatched")

    pressure = command_fn(["memory_pressure"])
    match = MEMORY_FREE.search(pressure or "")
    memory_free_percent = int(match.group(1)) if match else None
    if memory_free_percent is not None and not 0 <= memory_free_percent <= 100:
        memory_free_percent = None

    return {
        "schema_version": 1,
        "kind": "local_mac_ollama_preflight",
        "timestamp_utc": timestamp,
        "status": ("preflight_checks_passed_slot_verification_required"
                   if not problems else "preflight_blocked"),
        "expected": {"model": model, "model_digest": expected_model_digest,
                     "configured_parallelism": expected_parallelism},
        "observed": {"host_system": system, "power_source": power_source,
                     "ollama_version": version_text, "installed_model_digest": found_digest,
                     "gateway_live": gateway_live,
                     "ollama_process_count": len(pids),
                     "configured_parallelism": configured_parallelism,
                     "system_memory_free_percent": memory_free_percent},
        "blocking_reasons": problems,
        "active_slots_verified": False,
        "limitations": [
            "Active runtime slots (n_seq_max/n_slots) require independent operator log inspection",
            "Environment variables, self-reported versions and model digests do not attest active serving configuration",
            "This preflight performs no model inference and does not prove benchmark readiness or causality",
            "Memory free percentage is descriptive, not a memory-pressure or thermal attestation",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local macOS Ollama benchmark preflight")
    parser.add_argument("--expected-model-digest", required=True)
    parser.add_argument("--expected-parallelism", type=int, choices=(1, 2), required=True)
    parser.add_argument("--output", type=Path, help="Optional private JSON under reports/local; refuses overwrite")
    args = parser.parse_args(argv)
    try:
        if args.output is not None and args.output.suffix != ".json":
            raise ValueError("preflight output must be JSON")
        report = collect(expected_model_digest=args.expected_model_digest,
                         expected_parallelism=args.expected_parallelism)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2, sort_keys=True)
                output.write("\n")
        print(f"Preflight: {report['status']}")
        print(f"Power: {report['observed']['power_source']}; "
              f"Ollama: {report['observed']['ollama_version'] or 'unknown'}; "
              f"configured parallelism: {report['observed']['configured_parallelism']}")
        print("Active slots: NOT VERIFIED (inspect Ollama n_seq_max/n_slots logs)")
        for reason in report["blocking_reasons"]:
            print(f"BLOCKER: {reason}")
        if args.output is not None:
            print(f"Private preflight report: {args.output}")
        return 0 if not report["blocking_reasons"] else 2
    except (OSError, ValueError) as exc:
        print(f"Preflight rejected: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
