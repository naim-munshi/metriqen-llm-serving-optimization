"""Opt-in one-request P5 local recovery stream/admission smoke.

No service lifecycle changes, no model pulls/approval, no login or secrets.
A real local stream is explicitly initiated only with --execute.
Private evidence deliberately contains no prompts, tokens or responses.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tools.demo import real_ollama as real

ADMISSION = "http://127.0.0.1:8081/metrics/admission"
CHAT = real.GATEWAY + "/v1/chat/completions"
INFERENCE = real.GATEWAY + "/metrics/inference"
UPSTREAM = real.GATEWAY + "/health/upstream"
OLLAMA_TAGS = real.OLLAMA + "/api/tags"
OLLAMA_LOADED = real.OLLAMA + "/api/ps"
MAX_ADMISSION_BYTES = 16384
PROMPT = "Reply with the single word READY."


class SmokeBlocked(ValueError):
    """A fixed diagnostic code, never a model or HTTP response body."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_local(method: str, url: str, *, payload: dict | None = None,
                stream: bool = False) -> dict | bytes:
    """Only one additional bounded, unauthenticated metrics GET is permitted."""
    if method == "GET" and url == ADMISSION and payload is None and not stream:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        req = Request(ADMISSION, method="GET", headers={"Accept": "application/json"})
        try:
            with opener.open(req, timeout=5) as response:
                if (response.geturl() != ADMISSION or response.status != 200
                        or response.headers.get_content_type() != "application/json"):
                    raise SmokeBlocked("admission_metrics_transport_invalid")
                raw = response.read(MAX_ADMISSION_BYTES + 1)
                if len(raw) > MAX_ADMISSION_BYTES:
                    raise SmokeBlocked("admission_metrics_oversized")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise SmokeBlocked("admission_metrics_not_object")
                return result
        except HTTPError as exc:
            exc.close()
            raise SmokeBlocked("admission_metrics_http_status") from None
        except (URLError, OSError, TimeoutError):
            raise SmokeBlocked("admission_metrics_unreachable") from None
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, SmokeBlocked):
                raise
            raise SmokeBlocked("admission_metrics_invalid_json") from None
    if (method, url) not in real.ALLOWED:
        raise SmokeBlocked("endpoint_not_allowlisted")
    return real.fetch_local(method, url, payload=payload, stream=stream)


OUTCOME_TERMINALS = (
    "nonstream_complete_total", "stream_clean_eof_total",
    "pre_response_failure_total", "post_response_interrupted_total",
    "client_canceled_total",
)
ADMISSION_CUMULATIVE = (
    "admitted_immediate_total", "admitted_queued_total",
    "rejected_full_total", "queue_timeout_total", "queue_canceled_total",
    "queue_wait_observations_total",
)


def count(doc: dict, field: str) -> int:
    value = doc.get(field)
    if type(value) is not int or value < 0:
        raise SmokeBlocked("invalid_metric_" + field)
    return value


def validate_inference(doc: object) -> None:
    if not isinstance(doc, dict) or doc.get("schema_version") != 3:
        raise SmokeBlocked("inference_metrics_schema_mismatch")
    attempts = count(doc, "attempts_total")
    active = count(doc, "active_attempts")
    sums = sum(count(doc, field) for field in OUTCOME_TERMINALS)
    clean = count(doc, "stream_clean_eof_total")
    if (attempts != active + sums
            or clean != count(doc, "stream_done_total") + count(doc, "stream_missing_done_total")
            or count(doc, "duration_observations_total") != attempts - active):
        raise SmokeBlocked("inference_metrics_inconsistent")
    bins = doc.get("duration_bucket_counts")
    if (not isinstance(bins, list) or len(bins) != 9
            or any(type(x) is not int or x < 0 for x in bins)
            or sum(bins) != count(doc, "duration_observations_total")):
        raise SmokeBlocked("inference_duration_histogram_invalid")


def validate_admission(doc: object) -> None:
    if not isinstance(doc, dict) or doc.get("schema_version") != 2:
        raise SmokeBlocked("admission_metrics_schema_mismatch")
    if (count(doc, "active_requests") > count(doc, "max_active_requests")
            or count(doc, "waiting_requests") > count(doc, "max_waiting_requests")):
        raise SmokeBlocked("admission_gauges_inconsistent")
    for field in ADMISSION_CUMULATIVE:
        count(doc, field)


def collect(*, model: str, digest: str, fetch=fetch_local) -> dict:
    if not isinstance(model, str) or real.MODEL_NAME.fullmatch(model) is None:
        raise SmokeBlocked("model_tag_invalid")
    if not isinstance(digest, str) or real.DIGEST.fullmatch(digest) is None:
        raise SmokeBlocked("model_digest_invalid")

    health = fetch("GET", UPSTREAM)
    if not isinstance(health, dict) or health.get("status") != "reachable":
        raise SmokeBlocked("gateway_upstream_not_reachable")
    tags = fetch("GET", OLLAMA_TAGS)
    real._installed_model(tags.get("models") if isinstance(tags, dict) else None, model, digest)

    before_inf, before_adm = fetch("GET", INFERENCE), fetch("GET", ADMISSION)
    validate_inference(before_inf)
    validate_admission(before_adm)
    if (count(before_inf, "active_attempts") != 0
            or count(before_adm, "active_requests") != 0
            or count(before_adm, "waiting_requests") != 0):
        raise SmokeBlocked("other_gateway_requests_active")

    chat = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True, "temperature": 0, "max_tokens": 24,
    }
    stream = fetch("POST", CHAT, payload=chat, stream=True)
    if not isinstance(stream, bytes):
        raise SmokeBlocked("stream_response_invalid")
    real._check_stream(stream)

    after_adm, after_inf = fetch("GET", ADMISSION), fetch("GET", INFERENCE)
    validate_inference(after_inf)
    validate_admission(after_adm)
    loaded = fetch("GET", OLLAMA_LOADED)
    real._installed_model(loaded.get("models") if isinstance(loaded, dict) else None, model, digest)

    if (count(after_inf, "active_attempts") != 0
            or count(after_adm, "active_requests") != 0
            or count(after_adm, "waiting_requests") != 0):
        raise SmokeBlocked("gateway_slots_not_released")

    expected_inf = {
        "attempts_total": 1,
        "stream_done_total": 1,
        "stream_clean_eof_total": 1,
        "duration_observations_total": 1,
    }
    unchanged_inf = (
        "nonstream_complete_total", "stream_missing_done_total",
        "pre_response_failure_total", "post_response_interrupted_total",
        "client_canceled_total",
    )
    for field, delta in expected_inf.items():
        if count(after_inf, field) != count(before_inf, field) + delta:
            raise SmokeBlocked("stream_inference_counters_unmatched")
    if any(count(after_inf, field) != count(before_inf, field) for field in unchanged_inf):
        raise SmokeBlocked("unexpected_inference_outcome")
    if (count(after_adm, "admitted_immediate_total")
            + count(after_adm, "admitted_queued_total")
            != count(before_adm, "admitted_immediate_total")
            + count(before_adm, "admitted_queued_total") + 1):
        raise SmokeBlocked("stream_admission_not_recorded")
    if any(count(after_adm, field) != count(before_adm, field) for field in
           ("rejected_full_total", "queue_timeout_total", "queue_canceled_total")):
        raise SmokeBlocked("unexpected_admission_failure")

    return {
        "schema_version": 1,
        "kind": "operator_local_p5_post_recovery_sse_admission_observation",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gateway": "127.0.0.1:8081",
        "model_tag": model,
        "model_digest_prefix": digest[:12],
        "checks_passed": [
            "fixed_loopback_upstream_reachable",
            "original_installed_and_loaded_digest_pinned",
            "before_and_after_inference_counter_invariants",
            "before_and_after_admission_counter_schema",
            "real_sse_delta_and_terminal_done",
            "exactly_one_admitted_stream_attempt",
            "gateway_active_slots_released_after_stream",
            "no_new_gateway_failure_or_rejection_counters",
        ],
        "passed": True,
        "performance_benchmark": False,
        "same_gateway_pid_verified": False,
        "outage_failure_and_recovery_verified_by_this_tool": False,
        "client_cancellation_tested": False,
        "p5_reliability_security_gate_complete": False,
        "limitations": [
            "One bounded streaming chat on current local running gateway; no process changes",
            "Counters reset on gateway restart and must be interpreted with an idle gateway",
            "Prior outage and PID continuity are separate operator-sourced observations",
            "No client cancellation, model accuracy, TTFT, throughput, or production tenant authorization evidence",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in P5 one-stream release and SSE evidence")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--model", default="llama3.2:1b")
    parser.add_argument("--expected-digest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not args.execute:
        if args.output is not None:
            parser.error("--output requires --execute")
        print("PLAN ONLY: one bounded real local SSE chat plus read-only counters.")
        print("No services, deployments, experiments or model configurations changed.")
        return 0
    if args.expected_digest is None or args.output is None:
        print("P5 stream smoke blocked: pinned_digest_and_private_output_required", file=sys.stderr)
        return 2
    try:
        result = collect(model=args.model, digest=args.expected_digest)
        real.private_report(args.output, result)
    except (real.AcceptanceFailure, SmokeBlocked) as exc:
        print("P5 stream smoke blocked: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print("P5 stream smoke blocked: invalid_local_report_or_runtime", file=sys.stderr)
        return 2
    print("P5 bounded post-recovery SSE and admission release: PASS")
    print("Exactly one verified SSE [DONE] and one admitted stream; active gateway slots returned to zero.")
    print("Private sanitized report written; no model output logged.")
    print("P5 cancellation, security and portfolio signoff: NOT YET COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
