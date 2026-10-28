"""Opt-in, bounded real Mac gateway SSE client cancellation and slot-recovery smoke.

Default mode is zero-network. Explicit --execute sends one short streaming
prompt, closes the client socket after its first content delta, and checks
process-local terminal accounting followed by one short recovered JSON chat.
Does not stop/start services, touch model registry or emit prompt/response.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import sys
import time

from tools.demo import p5_stream_admission_smoke as metrics
from tools.demo import real_ollama as real

HOST = "127.0.0.1"
PORT = 8081
MODEL_DEFAULT = "llama3.2:1b"
CANCEL_PROMPT = "Write a numbered list of 300 short words, one per line."
VERIFY_PROMPT = "Reply READY"
READ_CAP = 128 * 1024
MAX_LINES = 200
STREAM_MAX_TOKENS = 256
ENDPOINT = "/v1/chat/completions"


class CancelBlocked(ValueError):
    """Sanitized, fixed problem codes only, never response text."""


def _count(doc: dict, field: str) -> int:
    return metrics.count(doc, field)


def cancel_after_first_delta(*, model: str) -> None:
    """Close first client connection promptly after first real SSE text delta."""
    if not isinstance(model, str) or real.MODEL_NAME.fullmatch(model) is None:
        raise CancelBlocked("model_tag_invalid")
    data = json.dumps({
        "model": model, "messages": [{"role": "user", "content": CANCEL_PROMPT}],
        "stream": True, "max_tokens": STREAM_MAX_TOKENS, "temperature": 0,
    }, separators=(",", ":"))
    conn = http.client.HTTPConnection(HOST, PORT, timeout=40)
    try:
        conn.request("POST", ENDPOINT, body=data,
                     headers={"Content-Type": "application/json",
                              "Accept": "text/event-stream",
                              "Connection": "close"})
        response = conn.getresponse()
        if response.status != 200:
            raise CancelBlocked("stream_http_status_not_200")
        if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
            raise CancelBlocked("stream_content_type_invalid")
        received = 0
        for _ in range(MAX_LINES):
            line = response.readline(8193)
            received += len(line)
            if len(line) > 8192 or received > READ_CAP:
                raise CancelBlocked("stream_before_first_delta_exceeded_limit")
            if not line:
                raise CancelBlocked("stream_ended_before_cancel")
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                raise CancelBlocked("stream_ended_before_cancel")
            try:
                packet = json.loads(raw)
                choices = packet.get("choices")
                delta = choices[0].get("delta") if (
                    isinstance(choices, list) and choices
                    and isinstance(choices[0], dict)
                ) else None
            except (ValueError, TypeError, AttributeError):
                raise CancelBlocked("stream_event_invalid") from None
            if isinstance(delta, dict) and isinstance(delta.get("content"), str) and delta["content"]:
                # Return immediately; finally closes the client socket.
                return
        raise CancelBlocked("stream_no_delta_within_limit")
    except (OSError, TimeoutError, http.client.HTTPException):
        raise CancelBlocked("stream_transport_unavailable") from None
    finally:
        conn.close()


def _metric_pair(fetch) -> tuple[dict, dict]:
    inf = fetch("GET", metrics.INFERENCE)
    adm = fetch("GET", metrics.ADMISSION)
    metrics.validate_inference(inf)
    metrics.validate_admission(adm)
    return inf, adm


def _idle(inf: dict, adm: dict) -> bool:
    return all(_count(doc, key) == 0 for doc, key in (
        (inf, "active_attempts"), (adm, "active_requests"), (adm, "waiting_requests")))


def collect(*, model: str, digest: str,
            fetch=metrics.fetch_local,
            stream_once=cancel_after_first_delta,
            sleep=time.sleep) -> dict:
    if not isinstance(model, str) or real.MODEL_NAME.fullmatch(model) is None:
        raise CancelBlocked("model_tag_invalid")
    if not isinstance(digest, str) or real.DIGEST.fullmatch(digest) is None:
        raise CancelBlocked("expected_digest_invalid")

    health = fetch("GET", metrics.UPSTREAM)
    if not isinstance(health, dict) or health.get("status") != "reachable":
        raise CancelBlocked("upstream_unreachable")
    for endpoint in (metrics.OLLAMA_TAGS, metrics.OLLAMA_LOADED):
        models = fetch("GET", endpoint)
        real._installed_model(models.get("models") if isinstance(models, dict) else None,
                              model, digest)

    start_inf, start_adm = _metric_pair(fetch)
    if not _idle(start_inf, start_adm):
        raise CancelBlocked("gateway_not_idle_before_test")
    stream_once(model=model)

    cancelled_inf = cancelled_adm = None
    for _ in range(40):
        inf, adm = _metric_pair(fetch)
        if (_count(inf, "attempts_total") == _count(start_inf, "attempts_total") + 1
                and _count(inf, "client_canceled_total") == _count(start_inf, "client_canceled_total") + 1
                and _idle(inf, adm)):
            cancelled_inf, cancelled_adm = inf, adm
            break
        sleep(0.25)
    if cancelled_inf is None or cancelled_adm is None:
        raise CancelBlocked("client_cancellation_not_observed")

    for field in ("stream_done_total", "stream_missing_done_total",
                  "stream_clean_eof_total", "pre_response_failure_total",
                  "post_response_interrupted_total", "nonstream_complete_total"):
        if _count(cancelled_inf, field) != _count(start_inf, field):
            raise CancelBlocked("canceled_stream_misclassified")
    if (_count(cancelled_adm, "admitted_immediate_total")
            + _count(cancelled_adm, "admitted_queued_total")
            != _count(start_adm, "admitted_immediate_total")
            + _count(start_adm, "admitted_queued_total") + 1):
        raise CancelBlocked("canceled_stream_admission_unmatched")

    response = fetch("POST", metrics.CHAT, payload={
        "model": model,
        "messages": [{"role": "user", "content": VERIFY_PROMPT}],
        "stream": False, "temperature": 0, "max_tokens": 16,
    })
    if (not isinstance(response, dict) or response.get("model") != model
            or not real._nonempty_message(response, native=False)):
        raise CancelBlocked("post_cancel_inference_invalid")

    final_inf, final_adm = _metric_pair(fetch)
    if not _idle(final_inf, final_adm):
        raise CancelBlocked("gateway_not_idle_after_recovery")
    for field, expected in (
        ("attempts_total", 2),
        ("client_canceled_total", 1),
        ("nonstream_complete_total", 1),
        ("duration_observations_total", 2)):
        if _count(final_inf, field) != _count(start_inf, field) + expected:
            raise CancelBlocked("post_cancel_outcome_delta_invalid")
    for field in ("pre_response_failure_total", "post_response_interrupted_total",
                  "stream_clean_eof_total", "stream_done_total", "stream_missing_done_total"):
        if _count(final_inf, field) != _count(start_inf, field):
            raise CancelBlocked("post_cancel_unexpected_outcome")
    if (_count(final_adm, "admitted_immediate_total")
            + _count(final_adm, "admitted_queued_total")
            != _count(start_adm, "admitted_immediate_total")
            + _count(start_adm, "admitted_queued_total") + 2):
        raise CancelBlocked("post_cancel_admission_delta_invalid")
    for field in ("rejected_full_total", "queue_timeout_total", "queue_canceled_total"):
        if _count(final_adm, field) != _count(start_adm, field):
            raise CancelBlocked("post_cancel_rejection_delta_invalid")
    return {
        "schema_version": 1,
        "kind": "operator_local_p5_stream_cancel_recovery_observation",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_tag": model,
        "model_digest_prefix": digest[:12],
        "checks_passed": [
            "original_model_installed_and_loaded_digest_matched",
            "gateway_idle_before_and_after",
            "client_closed_connection_after_first_sse_content_delta",
            "client_canceled_outcome_incremented_exactly_once",
            "gateway_admission_released_after_client_cancel",
            "follow_up_nonstream_real_inference_succeeded",
            "no_unexpected_completion_or_rejection_outcome",
        ],
        "passed": True,
        "performance_benchmark": False,
        "p5_reliability_security_gate_complete": False,
        "explicit_unverified": [
            "end_to_end_server_side_ollama_task_termination",
            "upstream_502_error_body_redaction",
            "distributed_project_lease_recovery",
            "OIDC_PostgreSQL_Redis_multi_tenant_security",
            "operator_browser_mobile_accessibility",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in P5 single-stream client cancellation and recovery smoke")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--expected-digest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        if args.output is not None:
            parser.error("--output requires --execute")
        print("PLAN ONLY: one 256-token-capped SSE request canceled after first delta, then one 16-token-capped follow-up chat.")
        print("No service restart, no model approval or gateway configuration changes.")
        return 0
    if not args.expected_digest or not args.output:
        print("P5 cancel smoke blocked: pinned_digest_and_private_output_required", file=sys.stderr)
        return 2
    try:
        report = collect(model=args.model, digest=args.expected_digest)
        real.private_report(args.output, report)
    except (real.AcceptanceFailure, CancelBlocked) as exc:
        print("P5 cancel smoke blocked: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError):
        print("P5 cancel smoke blocked: invalid_runtime_or_private_output", file=sys.stderr)
        return 2
    print("P5 real single-stream client-cancel and resumed JSON inference: PASS")
    print("Exact gateway cancel + completion counters and released admission slots verified.")
    print("Private report saved without response content; full P5/security/release still pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
