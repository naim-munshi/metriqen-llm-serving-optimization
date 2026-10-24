"""Local-only, dependency-free load runner for OpenAI-compatible chat endpoints.

This tool reports *observations*, not GPU utilization, pricing, or model quality.
It never writes prompt contents or model responses to its output artifacts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_DATASET_BYTES = 1 << 20
MAX_PROMPTS = 500
MAX_RESPONSE_BYTES = 1 << 20


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_endpoint(endpoint: str) -> None:
    """Forbid remote inference and cross-host redirects in this dev-only tool."""
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.path != "/v1/chat/completions" or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or not parsed.port or parsed.port <= 0):
        raise ValueError("endpoint must be http://127.0.0.1:PORT/v1/chat/completions")


def read_dataset(path: Path) -> tuple[list[list[dict[str, Any]]], str]:
    size = path.stat().st_size
    if not 0 < size <= MAX_DATASET_BYTES:
        raise ValueError("dataset must be nonempty and at most 1 MiB")
    raw = path.read_bytes()
    samples = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"dataset line {line_number} contains invalid JSON") from exc
        if not isinstance(obj, dict) or set(obj) != {"messages"}:
            raise ValueError(f"dataset line {line_number} must contain only messages")
        messages = obj["messages"]
        if (not isinstance(messages, list) or not 1 <= len(messages) <= 32
                or any(not isinstance(m, dict) or not isinstance(m.get("role"), str)
                       or not m["role"] or "content" not in m for m in messages)):
            raise ValueError(f"dataset line {line_number} has invalid messages")
        samples.append(messages)
        if len(samples) > MAX_PROMPTS:
            raise ValueError("dataset supports at most 500 prompts")
    if not samples:
        raise ValueError("dataset has no valid prompts")
    return samples, hashlib.sha256(raw).hexdigest()


def percentile_nearest_rank(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    import math
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)]


@dataclass(frozen=True)
class Sample:
    status: int | None
    success: bool
    latency_ms: float
    ttft_ms: float | None
    completion_tokens: int | None
    prompt_tokens: int | None
    error: str | None = None


def _token_usage(payload: object) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        return None, None
    usage = payload["usage"]
    c, p = usage.get("completion_tokens"), usage.get("prompt_tokens")
    return (c if type(c) is int and c >= 0 else None,
            p if type(p) is int and p >= 0 else None)


def _consume_stream(response, start: float) -> tuple[float | None, int | None, int | None]:
    """Parse bounded SSE frames; reject in-band errors and incomplete responses.

    TTFT is measured at the first *content* delta, not a role-only event.
    The parser never retains generated text in a benchmark report.
    """
    first_token_ms = None
    completion_tokens = prompt_tokens = None
    total_bytes = 0
    data_lines: list[bytes] = []
    event_name = b""
    terminal_seen = False
    while True:
        line = response.readline(MAX_RESPONSE_BYTES - total_bytes + 1)
        if not line:
            break
        total_bytes += len(line)
        if total_bytes > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
        field = line.rstrip(b"\r\n")
        # [DONE] is terminal only after an orderly EOF. Previously returning
        # at the marker silently ignored later error frames, duplicate [DONE]
        # or arbitrary trailing output, inflating measured successes.
        if terminal_seen:
            if field:
                raise ValueError("sse_data_after_terminator")
            continue
        if field:
            if field.startswith(b"data:"):
                value = field[5:]
                data_lines.append(value[1:] if value.startswith(b" ") else value)
            elif field.startswith(b"event:"):
                event_name = field[6:].strip()
            # Comments and other SSE fields are permitted but not reported.
            continue
        if not data_lines:
            if event_name == b"error":
                raise ValueError("upstream_stream_error")
            event_name = b""
            continue
        data = b"\n".join(data_lines).strip()
        data_lines.clear()
        if event_name == b"error":
            raise ValueError("upstream_stream_error")
        event_name = b""
        if data == b"[DONE]":
            terminal_seen = True
            continue
        try:
            event = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid_sse_json") from exc
        if not isinstance(event, dict):
            raise ValueError("invalid_sse_event")
        if "error" in event:
            raise ValueError("upstream_stream_error")
        choices = event.get("choices")
        if choices is None:
            if not isinstance(event.get("usage"), dict):
                raise ValueError("invalid_sse_event")
            choices = []
        if not isinstance(choices, list):
            raise ValueError("invalid_sse_event")
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError("invalid_sse_event")
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise ValueError("invalid_sse_event")
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("invalid_sse_event")
            if content and first_token_ms is None:
                first_token_ms = (time.perf_counter() - start) * 1000
            if choice.get("finish_reason") in ("error", "content_filter"):
                raise ValueError("upstream_stream_error")
        c, p = _token_usage(event)
        if c is not None:
            completion_tokens = c
        if p is not None:
            prompt_tokens = p
    if not terminal_seen:
        raise ValueError("missing_stream_terminator")
    return first_token_ms, completion_tokens, prompt_tokens


def request_once(endpoint: str, model: str, messages: list[dict[str, Any]],
                 *, stream: bool, max_tokens: int, timeout: float,
                 include_usage: bool = True) -> Sample:
    started = time.perf_counter()
    body = {"model": model, "messages": messages, "stream": stream,
            "max_tokens": max_tokens, "temperature": 0}
    # OpenAI-compatible runtimes such as Ollama return token usage in a
    # final empty-choices SSE chunk only when explicitly requested.
    # Older runtimes may not support this optional request parameter.
    if stream and include_usage:
        body["stream_options"] = {"include_usage": True}
    payload = json.dumps(body, separators=(",", ":")).encode()
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(endpoint, data=payload, method="POST",
                      headers={"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"})
    status = None
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            if stream:
                if response.headers.get_content_type() != "text/event-stream":
                    raise ValueError("invalid_stream_content_type")
                first, completion, prompt = _consume_stream(response, started)
                if first is None:
                    raise ValueError("no_content_token")
            else:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("invalid_json_content_type")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("response_too_large")
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
                    raise ValueError("no_chat_choices")
                first = None  # TTFT is not available for non-streamed responses.
                completion, prompt = _token_usage(data)
            return Sample(status, True, (time.perf_counter() - started) * 1000,
                          first, completion, prompt)
    except HTTPError as exc:
        return Sample(exc.code, False, (time.perf_counter() - started) * 1000,
                      None, None, None, f"http_{exc.code}")
    except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return Sample(status, False, (time.perf_counter() - started) * 1000,
                      None, None, None, reason[:80])


def summarize(samples: list[Sample], wall_seconds: float, stream: bool) -> dict[str, Any]:
    succeeded = [s for s in samples if s.success]
    times = [s.latency_ms for s in succeeded]
    ttft = [s.ttft_ms for s in succeeded if s.ttft_ms is not None]
    token_coverage = bool(succeeded) and all(s.completion_tokens is not None for s in succeeded)
    total_tokens = sum(s.completion_tokens or 0 for s in succeeded) if token_coverage else None
    error_codes: dict[str, int] = {}
    for s in samples:
        if not s.success:
            key = s.error or "unknown"
            error_codes[key] = error_codes.get(key, 0) + 1
    return {
        "requests": len(samples), "successes": len(succeeded), "failures": len(samples) - len(succeeded),
        "success_rate": round(len(succeeded) / len(samples), 4) if samples else None,
        "wall_seconds": round(wall_seconds, 4),
        "requests_per_second": round(len(succeeded) / wall_seconds, 4) if wall_seconds > 0 else None,
        "latency_ms": {"p50": percentile_nearest_rank(times, .50), "p95": percentile_nearest_rank(times, .95)},
        "ttft_ms": {"p50": percentile_nearest_rank(ttft, .50), "p95": percentile_nearest_rank(ttft, .95)} if stream else None,
        "ttft_samples": len(ttft),
        "completion_token_coverage": len(succeeded) if token_coverage else sum(s.completion_tokens is not None for s in succeeded),
        "output_tokens": total_tokens,
        "output_tokens_per_second": round(total_tokens / wall_seconds, 4) if total_tokens is not None and wall_seconds > 0 else None,
        "errors": error_codes,
    }


def run(endpoint: str, model: str, samples: list[list[dict[str, Any]]],
        *, stream: bool, max_tokens: int, concurrency: int, requests: int,
        warmup: int, timeout: float, include_usage: bool = True) -> dict[str, Any]:
    # Warm-up is intentionally sequential and excluded from measurements.
    warmup_failures = 0
    for i in range(warmup):
        result = request_once(endpoint, model, samples[i % len(samples)], stream=stream,
                              max_tokens=max_tokens, timeout=timeout,
                              include_usage=include_usage)
        if not result.success:
            warmup_failures += 1
    if warmup_failures:
        raise RuntimeError(f"{warmup_failures}/{warmup} warm-up requests failed; refusing benchmark")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(request_once, endpoint, model, samples[i % len(samples)],
                               stream=stream, max_tokens=max_tokens, timeout=timeout,
                               include_usage=include_usage): i
                   for i in range(requests)}
        indexed = [(futures[future], future.result()) for future in as_completed(futures)]
    wall = time.perf_counter() - started
    observations = [sample for _, sample in sorted(indexed)]
    result = summarize(observations, wall, stream)
    result["observations"] = [{"index": i, **asdict(sample)} for i, sample in enumerate(observations)]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure a local inference gateway without logging prompts")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--no-stream-usage", action="store_true",
                        help="Omit stream_options.include_usage for older runtimes")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", required=True, help="Self-reported runtime/version")
    parser.add_argument("--hardware", required=True, help="Self-reported CPU/GPU specification")
    parser.add_argument("--model-revision", required=True, help="Model tag or immutable revision, if known")
    args = parser.parse_args(argv)
    try:
        validate_endpoint(args.endpoint)
        if not args.model.strip() or len(args.model) > 128:
            raise ValueError("model name must contain 1-128 characters")
        if not 1 <= args.requests <= 1000 or not 1 <= args.concurrency <= 4:
            raise ValueError("requests must be 1-1000 and concurrency 1-4 (gateway cap)")
        if not 0 <= args.warmup <= 100 or not 1 <= args.max_tokens <= 2048:
            raise ValueError("warmup 0-100 and max-tokens 1-2048 required")
        if not 0 < args.timeout <= 180:
            raise ValueError("timeout must be in (0, 180]")
        if not all(v.strip() for v in (args.runtime, args.hardware, args.model_revision)):
            raise ValueError("runtime, hardware and model-revision must be nonempty")
        if args.dataset.resolve() == args.output.resolve():
            raise ValueError("dataset and output paths must be different")
        samples, dataset_hash = read_dataset(args.dataset)
        clock_started = datetime.now(timezone.utc)
        elapsed_started = time.perf_counter()
        results = run(args.endpoint, args.model, samples, stream=args.stream,
                      max_tokens=args.max_tokens, concurrency=args.concurrency,
                      requests=args.requests, warmup=args.warmup, timeout=args.timeout,
                      include_usage=not args.no_stream_usage)
        clock_finished = datetime.now(timezone.utc)
        elapsed_seconds = time.perf_counter() - elapsed_started
        timing = {
            "started_at_utc": clock_started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "finished_at_utc": clock_finished.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "elapsed_seconds": round(elapsed_seconds, 4),
            "scope": "warmup_and_measured_requests",
        }
        report = {
            "schema_version": 1,
            "method": "locally_observed_wall_clock; throughput is completed-successes/wall-time; latency p95 nearest-rank on successful requests; warmups excluded",
            "environment": {"endpoint": args.endpoint, "model": args.model,
                            "model_revision": args.model_revision,
                            "runtime_self_reported": args.runtime,
                            "hardware_self_reported": args.hardware,
                            "dataset_sha256": dataset_hash},
            "configuration": {"requests": args.requests, "concurrency": args.concurrency,
                              "warmup": args.warmup, "stream": args.stream,
                              "stream_usage_requested": args.stream and not args.no_stream_usage,
                              "max_tokens": args.max_tokens, "timeout_seconds": args.timeout},
            "results": results,
            "timing": timing,
            "limitations": ["Hardware/model/runtime identifiers are caller-provided, not independently verified",
                            "No GPU memory utilization or tokenization performed by this tool",
                            "Streaming token usage is requested by default, but may be unavailable from some runtimes",
                            "Token throughput is unavailable unless every successful completion reports usage",
                            "Comparisons require identical model/revision, prompts, output caps, hardware and workload"],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{results['successes']}/{results['requests']} measured requests succeeded; report: {args.output}")
        return 0 if results["failures"] == 0 else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
