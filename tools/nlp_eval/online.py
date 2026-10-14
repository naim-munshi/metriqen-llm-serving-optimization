"""Local-only model-to-reference evaluation. No prompts/responses in reports."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tools.nlp_eval.evaluator import evaluate

MAX_DATASET_BYTES = 2 * 1024 * 1024
MAX_SAMPLES = 200
MAX_RESPONSE_BYTES = 1 << 20


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                 and parsed.port is not None and parsed.port > 0
                 and parsed.path == "/v1/chat/completions"
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
    except ValueError as exc:
        raise ValueError("invalid localhost endpoint") from exc
    if not valid:
        raise ValueError("endpoint must be http://127.0.0.1:PORT/v1/chat/completions")


def load_workload(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    if not 0 < len(raw) <= MAX_DATASET_BYTES:
        raise ValueError("dataset must be nonempty and at most 2 MiB")
    rows: list[dict] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"invalid JSON on line {line_number}") from exc
        if not isinstance(record, dict) or set(record) != {"id", "messages", "reference"}:
            raise ValueError(f"line {line_number} must contain id, messages, reference only")
        key, reference, messages = record["id"], record["reference"], record["messages"]
        if (not isinstance(key, str) or not key.strip() or len(key) > 128
                or not isinstance(reference, str) or len(reference) > 8192):
            raise ValueError(f"invalid id or reference on line {line_number}")
        if key in seen:
            raise ValueError(f"duplicate id on line {line_number}")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
            raise ValueError(f"invalid messages on line {line_number}")
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or not isinstance(message["role"], str)
                    or message["role"] not in {"system", "user", "assistant"}
                    or not isinstance(message["content"], str)
                    or not 1 <= len(message["content"]) <= 8192):
                raise ValueError(f"invalid message on line {line_number}")
        rows.append(record)
        seen.add(key)
        if len(rows) > MAX_SAMPLES:
            raise ValueError("dataset exceeds 200 records")
    if not rows:
        raise ValueError("dataset contains no examples")
    return rows, hashlib.sha256(raw).hexdigest()


def generate_one(endpoint: str, model: str, messages: list[dict],
                 *, max_tokens: int, timeout: float) -> tuple[str, float]:
    """Fetch one nonstream completion; never expose upstream error bodies."""
    data = json.dumps({"model": model, "messages": messages, "stream": False,
                       "temperature": 0, "max_tokens": max_tokens},
                      separators=(",", ":")).encode()
    request = Request(endpoint, data=data, method="POST",
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise ValueError("unexpected upstream status or content type")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("upstream response too large")
            result = json.loads(raw)
    except HTTPError as exc:
        # HTTPError is also a response-like object. Explicitly close its
        # body (including rejected redirects/429s) before discarding it.
        # Never read or expose an untrusted upstream error body.
        status = exc.code
        exc.close()
        raise ValueError(f"upstream HTTP {status}") from None
    except (URLError, OSError, TimeoutError) as exc:
        raise ValueError(f"upstream transport error: {type(exc).__name__}") from None
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid upstream response") from None
    if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
        raise ValueError("upstream returned no chat choices")
    choice = result["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("upstream returned malformed chat choice")
    answer = choice["message"].get("content")
    if not isinstance(answer, str):
        raise ValueError("upstream returned non-text answer")
    return answer, (time.perf_counter() - started) * 1000


def run(endpoint: str, model: str, rows: list[dict],
        *, max_tokens: int, timeout: float) -> dict:
    """Abort on any failed generation rather than misreporting partial quality."""
    references: dict[str, str] = {}
    predictions: dict[str, str] = {}
    latencies: list[float] = []
    for row in rows:
        answer, latency = generate_one(endpoint, model, row["messages"],
                                       max_tokens=max_tokens, timeout=timeout)
        references[row["id"]] = row["reference"]
        predictions[row["id"]] = answer
        latencies.append(latency)
    quality = evaluate(references, predictions)
    latencies.sort()
    import math
    return {
        "quality": quality,
        "generation": {"successful_requests": len(rows), "failed_requests": 0,
                       "client_latency_p50_ms": latencies[math.ceil(len(latencies) * .50) - 1],
                       "client_latency_p95_ms": latencies[math.ceil(len(latencies) * .95) - 1]},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate local chat answers against references")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--hardware", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        validate_endpoint(args.endpoint)
        if not 1 <= args.max_tokens <= 2048 or not 0 < args.timeout <= 180:
            raise ValueError("invalid token cap or timeout")
        if not args.model.strip() or len(args.model) > 128:
            raise ValueError("invalid model name")
        if not all(v.strip() for v in (args.model_revision, args.runtime, args.hardware)):
            raise ValueError("model revision, runtime and hardware must be specified")
        if args.output.resolve() == args.dataset.resolve():
            raise ValueError("output path cannot overwrite dataset")
        rows, dataset_hash = load_workload(args.dataset)
        measurements = run(args.endpoint, args.model, rows,
                           max_tokens=args.max_tokens, timeout=args.timeout)
        report = {
            "schema_version": 1,
            "dataset_sha256": dataset_hash,
            "model": args.model,
            "model_revision_self_reported": args.model_revision,
            "runtime_self_reported": args.runtime,
            "hardware_self_reported": args.hardware,
            "decoding": {"temperature": 0, "max_tokens": args.max_tokens, "stream": False},
            "results": measurements,
            "limitations": [
                "Reference string scoring is not semantic or factuality evaluation",
                "Hardware/runtime/model revision metadata is user-supplied",
                "Non-streaming generation does not measure TTFT or GPU memory",
                "A generation failure aborts evaluation; no partial scores are emitted",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"Evaluated {len(rows)} local completions; report: {args.output}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
