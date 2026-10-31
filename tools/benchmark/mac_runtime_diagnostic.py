"""Read-only Mac runtime diagnostics before prospective P2/P3 experiments.

Unlike the strict benchmark preflight, this *diagnoses* the known blocked
case, without accepting it. It never starts a model, changes environment,
kills/restarts processes, reads local private evidence or runs inference.
Only small, fixed loopback JSON endpoints and named local commands are used.
Raw process environment/command output is NEVER stored or printed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import re
import subprocess
from typing import Callable
from urllib.request import ProxyHandler, Request, build_opener

from tools.benchmark.runner import NoRedirect

SERVER = "http://127.0.0.1:11434"
GATEWAY = "http://127.0.0.1:8081"
MODEL = "llama3.2:1b"
SAFE_VERSION = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,4}\Z")
CLIENT_VERSION = re.compile(r"(?im)^\s*Warning:\s*client version is\s+(\d{1,3}\.\d{1,3}\.\d{1,4})\s*$")
PARALLEL = re.compile(r"(?:^|\s)OLLAMA_NUM_PARALLEL=(\d+)(?=\s|$)")
LISTENER_PID = re.compile(r"p([1-9]\d{0,8})\Z")


def command(argv: list[str]) -> str | None:
    """Capture locally; redact by extraction before returning any public fields."""
    try:
        res = subprocess.run(argv, capture_output=True, text=True,
                             timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    # Ollama's version-skew warning may be emitted on stderr. Consume it
    # only for this exact non-sensitive version command; never append
    # stderr from ps/env or lsof, which may contain private details.
    if argv == ["ollama", "--version"]:
        return (res.stdout + "\\n" + res.stderr)[:8192]
    return res.stdout


def local_json(url: str) -> dict | None:
    if url not in (SERVER + "/api/version", SERVER + "/api/tags",
                   SERVER + "/api/ps", GATEWAY + "/health/live"):
        raise ValueError("not a fixed loopback diagnostic endpoint")
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(Request(url, headers={"Accept": "application/json"}),
                         timeout=3) as resp:
            if resp.headers.get_content_type() != "application/json":
                return None
            data = resp.read(65537)
            if len(data) > 65536:
                return None
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def collect(*, command_fn: Callable[[list[str]], str | None] = command,
            json_fn: Callable[[str], dict | None] = local_json,
            system: str | None = None) -> dict:
    os_name = system if system is not None else platform.system()
    pmset = command_fn(["pmset", "-g", "batt"]) or ""
    power = ("AC" if "Now drawing from 'AC Power'" in pmset
             else "Battery" if "Now drawing from 'Battery Power'" in pmset
             else "unknown")

    server = json_fn(SERVER + "/api/version")
    value = server.get("version") if isinstance(server, dict) else None
    server_version = value if isinstance(value, str) and SAFE_VERSION.fullmatch(value) else None

    # CLI may report the server version even when its own version differs.
    # Thus NEVER infer client version from the first "ollama version is" line.
    cli = command_fn(["ollama", "--version"]) or ""
    match = CLIENT_VERSION.search(cli)
    client_version = match.group(1) if match else None
    version_mismatch = (client_version != server_version
                        if client_version and server_version else None)

    pids_raw = command_fn([
        "lsof", "-nP", "-F", "p", "-iTCP:11434", "-sTCP:LISTEN"
    ]) or ""
    pids = sorted({int(m.group(1)) for line in pids_raw.splitlines()
                   if (m := LISTENER_PID.fullmatch(line.strip())) is not None})
    pid = pids[0] if len(pids) == 1 else None
    named_ollama = False
    parallel = None
    if pid is not None:
        comm = command_fn(["ps", "-p", str(pid), "-o", "comm="]) or ""
        # Only the derived boolean is retained: not the full executable path.
        named_ollama = Path(comm.strip()).name.lower() == "ollama"
        if named_ollama:
            env = command_fn(["ps", "eww", "-p", str(pid)]) or ""
            found = PARALLEL.search(env)
            if found:
                n = int(found.group(1))
                parallel = n if n in (1, 2) else None

    tags = json_fn(SERVER + "/api/tags")
    installed = (isinstance(tags, dict) and isinstance(tags.get("models"), list)
                 and any(isinstance(x, dict) and x.get("name") == MODEL
                         and isinstance(x.get("digest"), str)
                         and re.fullmatch("[0-9a-f]{64}", x["digest"])
                         for x in tags["models"]))
    loaded = json_fn(SERVER + "/api/ps")
    resident = (isinstance(loaded, dict) and isinstance(loaded.get("models"), list)
                and any(isinstance(x, dict) and x.get("name", x.get("model")) == MODEL
                        for x in loaded["models"]))
    health = json_fn(GATEWAY + "/health/live")
    gateway_live = (isinstance(health, dict)
                    and health.get("service") == "metriqen-inference-gateway"
                    and health.get("status") == "ok")

    blockers = []
    if os_name != "Darwin":
        blockers.append("macos_not_confirmed")
    if power != "AC":
        blockers.append("ac_power_required")
    if not server_version or len(pids) != 1 or not named_ollama:
        blockers.append("ollama_listener_identity_unverified")
    if version_mismatch is True:
        blockers.append("ollama_client_server_version_mismatch")
    if parallel not in (1, 2):
        blockers.append("ollama_parallelism_not_explicitly_verified")
    if not installed:
        blockers.append("model_tag_not_installed_verified")
    if not gateway_live:
        blockers.append("gateway_not_healthy")
    # Residency is descriptive: an idle Ollama normally unloads the model.
    # Runtime n_slots must *always* be manually attested separately.
    return {
        "schema_version": 1,
        "kind": "mac_readonly_runtime_diagnostic",
        "benchmark_ready": False,
        "benchmark_blockers": blockers,
        "observed": {
            "power": power, "server_version": server_version,
            "client_version_if_warning": client_version,
            "version_mismatch": version_mismatch,
            "ollama_listener_count": len(pids),
            "ollama_listener_named_process": named_ollama,
            "ollama_parallelism_configured": parallel,
            "model_installed": installed, "model_resident": resident,
            "gateway_healthy": gateway_live,
            "runtime_slots_attested": False,
        },
        "notes": [
            "Diagnostic only: it cannot mark P2 or P3 benchmark-ready",
            "Do not infer Ollama slots from the Go gateway's active-request cap",
            "No private environment, binary paths, model digests or raw process output are reported",
            "An unloaded installed model is not automatically a broken installation",
            "If the owner intentionally changes Ollama config, do so only through its verified owning supervisor",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Mac diagnostic; never starts benchmark or changes services")
    parser.parse_args(argv)
    report = collect()
    print("Mac P2/P3 read-only diagnostic; NOT benchmark approval")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0  # Diagnostic completion, NOT P2/P3 acceptance


if __name__ == "__main__":
    raise SystemExit(main())
