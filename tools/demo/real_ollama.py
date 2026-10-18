"""Operator-run, no-download real Ollama + Metriqen gateway portfolio smoke.

Requires existing locally running Ollama and Go gateway. Sends three short
inference requests, but never restarts services, pulls models, changes
configuration, or exposes private prompts/model responses in its report.

CI uses fake local HTTP fixtures ONLY; passing CI does not prove real
Ollama execution, Apple Metal, GPU performance, or model weights.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.client import HTTPResponse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
OLLAMA = "http://127.0.0.1:11434"
GATEWAY = "http://127.0.0.1:8081"
MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9._/-]*:[a-z0-9][a-z0-9._-]*\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 131072
MAX_SSE_BYTES = 262144
PROMPT = "In a short sentence, explain time to first token."
ALLOWED = frozenset({
    ("GET", OLLAMA + "/api/version"),
    ("GET", OLLAMA + "/api/tags"),
    ("GET", OLLAMA + "/api/ps"),
    ("POST", OLLAMA + "/api/chat"),
    ("GET", GATEWAY + "/health/live"),
    ("GET", GATEWAY + "/health/upstream"),
    ("GET", GATEWAY + "/metrics/inference"),
    ("POST", GATEWAY + "/v1/chat/completions"),
})


class AcceptanceFailure(ValueError):
    """Fixed diagnostic code, never a raw upstream response or token."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def fetch_local(method: str, url: str, *, payload: dict | None = None,
                bearer: str | None = None, stream: bool = False) -> dict | bytes:
    """Only fixed loopback URLs, no proxy, redirect or arbitrary host."""
    if (method, url) not in ALLOWED or (method == "POST") != (payload is not None):
        raise AcceptanceFailure("non_allowlisted_endpoint")
    if bearer is not None and url != GATEWAY + "/v1/chat/completions":
        raise AcceptanceFailure("credential_misrouted")
    if bearer is not None and (not bearer.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in bearer)):
        raise AcceptanceFailure("invalid_bearer_configuration")
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Accept": "text/event-stream" if stream else "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=120 if method == "POST" else 5) as response:
            kind = response.headers.get_content_type()
            if kind != ("text/event-stream" if stream else "application/json"):
                raise AcceptanceFailure("unexpected_content_type")
            limit = MAX_SSE_BYTES if stream else MAX_JSON_BYTES
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise AcceptanceFailure("response_exceeds_bound")
            if stream:
                return raw
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise AcceptanceFailure("response_not_json_object")
            return parsed
    except HTTPError as exc:
        exc.close()
        raise AcceptanceFailure("local_http_status_" + str(exc.code)) from None
    except (TimeoutError, URLError, OSError):
        raise AcceptanceFailure("local_runtime_unreachable") from None
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, AcceptanceFailure):
            raise
        raise AcceptanceFailure("invalid_local_json_response") from None


def _nonempty_message(data: dict, *, native: bool) -> bool:
    if native:
        value = data.get("message")
    else:
        choices = data.get("choices")
        value = choices[0].get("message") if (
            isinstance(choices, list) and choices
            and isinstance(choices[0], dict)
        ) else None
    return (isinstance(value, dict) and isinstance(value.get("content"), str)
            and bool(value["content"].strip()))


def _check_stream(raw: bytes) -> None:
    """Require a nonempty delta, one terminal [DONE], and no events after it."""
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise AcceptanceFailure("stream_invalid_utf8") from None
    if not text.endswith("\n\n"):
        raise AcceptanceFailure("stream_missing_final_separator")
    done = 0
    deltas = 0
    seen_final_usage = False
    for frame in text.split("\n\n"):
        if not frame:
            continue
        lines = [line[5:].lstrip(" ") for line in frame.split("\n") if line.startswith("data:")]
        if not lines:
            continue
        if done:
            raise AcceptanceFailure("stream_data_after_done")
        if len(lines) != 1:
            raise AcceptanceFailure("stream_multiline_data")
        if lines[0] == "[DONE]":
            done += 1
            continue
        if seen_final_usage:
            raise AcceptanceFailure("stream_data_after_usage")
        try:
            chunk = json.loads(lines[0])
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if choices == [] and isinstance(chunk.get("usage"), dict):
                # Optional, UNTRUSTED final usage event. It has no text,
                # must not be confused with the stream termination.
                seen_final_usage = True
                continue
            if not isinstance(choices, list) or not choices:
                raise ValueError("missing delta choices")
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if isinstance(delta, dict) and isinstance(delta.get("content"), str) and delta["content"].strip():
                deltas += 1
            if isinstance(chunk.get("error"), (str, dict)):
                raise ValueError("in-band model error")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AcceptanceFailure("stream_invalid_event") from None
    if done != 1:
        raise AcceptanceFailure("stream_missing_done")
    if not deltas:
        raise AcceptanceFailure("stream_without_model_delta")


def _installed_model(models: object, model: str, digest: str) -> None:
    if not isinstance(models, list):
        raise AcceptanceFailure("model_list_unavailable")
    matched = [v for v in models if isinstance(v, dict)
               and (v.get("name") == model or v.get("model") == model)]
    if len(matched) != 1 or matched[0].get("digest") != digest:
        raise AcceptanceFailure("model_digest_unmatched")


def _metric(metrics: dict, key: str) -> int:
    val = metrics.get(key)
    if type(val) is not int or val < 0:
        raise AcceptanceFailure("invalid_gateway_metric")
    return val


def collect(*, model: str, digest: str, bearer: str | None = None,
            fetch=fetch_local) -> dict:
    if not isinstance(model, str) or MODEL_NAME.fullmatch(model) is None:
        raise AcceptanceFailure("invalid_model_name")
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        raise AcceptanceFailure("invalid_expected_digest")

    # Check the installed digest BEFORE and loaded digest AFTER requests.
    version = fetch("GET", OLLAMA + "/api/version")
    if (not isinstance(version, dict) or not isinstance(version.get("version"), str)
            or not version["version"].strip()):
        raise AcceptanceFailure("ollama_version_unavailable")
    tags = fetch("GET", OLLAMA + "/api/tags")
    _installed_model(tags.get("models") if isinstance(tags, dict) else None, model, digest)

    live = fetch("GET", GATEWAY + "/health/live")
    if not isinstance(live, dict) or live.get("service") != "metriqen-inference-gateway" or live.get("status") != "ok":
        raise AcceptanceFailure("gateway_liveness_unavailable")
    upstream = fetch("GET", GATEWAY + "/health/upstream")
    if not isinstance(upstream, dict) or upstream.get("status") != "reachable":
        raise AcceptanceFailure("gateway_upstream_unreachable")
    before = fetch("GET", GATEWAY + "/metrics/inference")
    if not isinstance(before, dict):
        raise AcceptanceFailure("gateway_metrics_unavailable")

    native = fetch("POST", OLLAMA + "/api/chat", payload={
        "model": model, "messages": [{"role": "user", "content": PROMPT}],
        "stream": False, "options": {"num_predict": 32, "temperature": 0},
    })
    if (not isinstance(native, dict) or native.get("model") != model
            or native.get("done") is not True or not _nonempty_message(native, native=True)):
        raise AcceptanceFailure("native_generation_invalid")

    chat = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "stream": False, "temperature": 0, "max_tokens": 32}
    nonstream = fetch("POST", GATEWAY + "/v1/chat/completions", payload=chat, bearer=bearer)
    if (not isinstance(nonstream, dict) or nonstream.get("model") != model
            or not _nonempty_message(nonstream, native=False)):
        raise AcceptanceFailure("gateway_nonstream_generation_invalid")

    chat["stream"] = True
    frames = fetch("POST", GATEWAY + "/v1/chat/completions",
                   payload=chat, bearer=bearer, stream=True)
    if not isinstance(frames, bytes):
        raise AcceptanceFailure("stream_not_bytes")
    _check_stream(frames)

    loaded = fetch("GET", OLLAMA + "/api/ps")
    _installed_model(loaded.get("models") if isinstance(loaded, dict) else None, model, digest)
    tags_after = fetch("GET", OLLAMA + "/api/tags")
    _installed_model(tags_after.get("models") if isinstance(tags_after, dict) else None,
                     model, digest)
    after = fetch("GET", GATEWAY + "/metrics/inference")
    if not isinstance(after, dict):
        raise AcceptanceFailure("gateway_metrics_unavailable")
    for key, delta in (("attempts_total", 2), ("nonstream_complete_total", 1),
                       ("stream_done_total", 1)):
        if _metric(after, key) < _metric(before, key) + delta:
            raise AcceptanceFailure("gateway_metric_did_not_increment")
    return {
        "schema_version": 1,
        "kind": "operator_local_real_model_smoke_observation",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_tag": model,
        "model_digest": digest,
        "ollama_version_reported": version["version"],
        "gateway": "127.0.0.1:8081",
        "tests_passed": [
            "installed_model_digest_before_and_after",
            "ollama_native_nonstream_chat",
            "gateway_nonstream_chat",
            "gateway_streaming_delta_and_done",
            "matching_loaded_digest_after",
            "gateway_two_completed_attempt_metrics",
        ],
        "passed": True,
        "model_inference_http_observed": True,
        "actual_gpu_execution_verified": False,
        "performance_benchmark": False,
        "limitations": [
            "Local HTTP model behavior and digest metadata are not hardware or cryptographic attestation",
            "This is a three-call smoke test, not an A/B/A/B or p95/TTFT benchmark",
            "OIDC, cross-tenant inference and browser deployments are outside this local gateway path",
        ],
    }


def private_report(path: Path, result: dict) -> None:
    base = (ROOT / "reports" / "local").resolve()
    if path.suffix != ".json" or not path.resolve().is_relative_to(base):
        raise AcceptanceFailure("report_requires_private_reports_local_json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_EXCL avoids overwrites/symlink replacements; created mode 0600.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        raise AcceptanceFailure("private_report_create_failed") from None
    with os.fdopen(fd, "w", encoding="utf-8") as sink:
        json.dump(result, sink, sort_keys=True, indent=2)
        sink.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real local Ollama + Go gateway portfolio acceptance")
    parser.add_argument("--model", default="llama3.2:1b")
    parser.add_argument("--expected-digest", required=True,
                        help="64 lowercase hex chars copied from local /api/tags")
    parser.add_argument("--output", type=Path, help="New private JSON under reports/local/ only")
    args = parser.parse_args(argv)
    try:
        # Read only the locally configured *development* shared bearer when
        # the user already opted into that gateway mode. Never print it.
        token = os.environ.get("METRIQEN_GATEWAY_BEARER_TOKEN") or None
        result = collect(model=args.model, digest=args.expected_digest, bearer=token)
        if args.output is not None:
            private_report(args.output, result)
        print("Real local Ollama + Go gateway smoke: PASS")
        print("Checks: native generation, gateway JSON/SSE, loaded digest, gateway counters.")
        print("Not verified: GPU acceleration, throughput/TTFT/p95, multi-tenant deployment.")
        if args.output is not None:
            print("Private evidence report created under reports/local/")
        return 0
    except AcceptanceFailure as exc:
        print("Real local Ollama smoke blocked: " + str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print("Real local Ollama smoke blocked: invalid local configuration", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
