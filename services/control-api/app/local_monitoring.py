"""Bounded read-only snapshot of fixed localhost Go inference-gateway counters.

Process-local counts are NOT throughput, end-to-end latency percentiles,
TTFT, uptime, fleet totals, GPU measurements, or benchmark evidence.
No request/model/prompt/user labels are collected or persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import HTTPException


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# This is the real direct localhost gateway, not the optional HAProxy front-end.
# The origin and three routes are fixed; browser clients cannot select a target.
_GATEWAY_ORIGIN = "http://127.0.0.1:8081"
_PATH_SCHEMAS = {
    "/metrics/inference": 3,
    "/metrics/admission": 2,
    "/metrics/upstream": 1,
}
_INFERENCE_COUNTERS = (
    "attempts_total", "active_attempts", "nonstream_complete_total",
    "stream_clean_eof_total", "stream_done_total", "stream_missing_done_total",
    "pre_response_failure_total", "post_response_interrupted_total",
    "client_canceled_total", "duration_observations_total", "duration_total_us",
)
_ADMISSION_COUNTERS = (
    "active_requests", "waiting_requests", "max_active_requests",
    "max_waiting_requests", "admitted_immediate_total", "admitted_queued_total",
    "rejected_full_total", "queue_timeout_total", "queue_canceled_total",
    "queue_wait_observations_total", "queue_wait_duration_total_us",
)
_UPSTREAM_COUNTERS = (
    "connection_refused_total", "transport_errors_total", "timeouts_total",
    "http_rejected_total", "http_other_status_total", "protocol_errors_total",
    "response_too_large_total", "stream_read_errors_total",
    "stream_size_limit_total",
)


def _count(value: object, name: str) -> int:
    # bool is an int subclass in Python but must not be accepted as a counter.
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError("invalid local gateway metric " + name)
    return value


class GatewayMonitoring:
    """Direct read-only local gateway metrics; never sends operator credentials."""

    ORIGIN = _GATEWAY_ORIGIN

    def __init__(self) -> None:
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _fetch(self, path: str, expected_version: int, keys: tuple[str, ...]) -> dict:
        if path not in _PATH_SCHEMAS or _PATH_SCHEMAS[path] != expected_version:
            raise ValueError("unsupported local monitoring route")
        request = Request(
            self.ORIGIN + path, method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=2) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("unexpected gateway metrics content type")
                data = response.read(16385)
                if len(data) > 16384:
                    raise ValueError("gateway metrics body too large")
                decoded = json.loads(data)
            if not isinstance(decoded, dict):
                raise ValueError("gateway metrics must be an object")
            if _count(decoded.get("schema_version"), "schema_version") != expected_version:
                raise ValueError("unexpected gateway metrics schema version")
            return {key: _count(decoded.get(key), key) for key in keys}
        except HTTPError as exc:
            # Failed localhost metric reads must not retain an open
            # urllib HTTPError body, or expose internal upstream data.
            try:
                raise HTTPException(status_code=503, detail="local inference monitoring unavailable") from None
            finally:
                exc.close()
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=503, detail="local inference monitoring unavailable") from None

    def snapshot(self) -> dict:
        inference = self._fetch("/metrics/inference", 3, _INFERENCE_COUNTERS)
        admission = self._fetch("/metrics/admission", 2, _ADMISSION_COUNTERS)
        upstream = self._fetch("/metrics/upstream", 1, _UPSTREAM_COUNTERS)
        if (inference["attempts_total"] <
                inference["active_attempts"] +
                inference["nonstream_complete_total"] +
                inference["stream_clean_eof_total"] +
                inference["pre_response_failure_total"] +
                inference["post_response_interrupted_total"] +
                inference["client_canceled_total"] or
                inference["stream_done_total"] + inference["stream_missing_done_total"] !=
                inference["stream_clean_eof_total"] or
                inference["duration_observations_total"] > inference["attempts_total"] or
                admission["active_requests"] > admission["max_active_requests"] or
                admission["waiting_requests"] > admission["max_waiting_requests"]):
            raise HTTPException(status_code=503, detail="local inference monitoring counters inconsistent")
        samples = inference["duration_observations_total"]
        # Duration starts after admission and includes downstream writes.
        # No invented percentile or TTFT is returned.
        mean_ms = (round(inference["duration_total_us"] / samples / 1000, 2)
                   if samples else None)
        return {
            "schema_version": 1,
            "source": "local_gateway_8081",
            "observed_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "process_local_counters": True,
            "inference": {
                "admitted_attempts_total": inference["attempts_total"],
                "active_attempts": inference["active_attempts"],
                "nonstream_complete_total": inference["nonstream_complete_total"],
                "stream_clean_eof_total": inference["stream_clean_eof_total"],
                "stream_missing_done_total": inference["stream_missing_done_total"],
                "pre_response_failure_total": inference["pre_response_failure_total"],
                "post_response_interrupted_total": inference["post_response_interrupted_total"],
                "client_canceled_total": inference["client_canceled_total"],
                "duration_observations_total": samples,
                "mean_post_admission_duration_ms": mean_ms,
            },
            "admission": {
                "active_requests": admission["active_requests"],
                "waiting_requests": admission["waiting_requests"],
                "max_active_requests": admission["max_active_requests"],
                "max_waiting_requests": admission["max_waiting_requests"],
                "admitted_immediate_total": admission["admitted_immediate_total"],
                "admitted_queued_total": admission["admitted_queued_total"],
                "rejected_full_total": admission["rejected_full_total"],
                "queue_timeout_total": admission["queue_timeout_total"],
                "queue_canceled_total": admission["queue_canceled_total"],
            },
            "upstream": {
                "failure_events_total": sum(upstream.values()),
                "failure_breakdown": upstream,
            },
            "limitations": (
                "Process-local counters reset when Go gateway restarts; "
                "admission rejections are not in admitted inference attempts; "
                "upstream failure events are not one-to-one inference failures; "
                "mean duration excludes FIFO wait; no TTFT, p95 or request rate is measured"
            ),
        }
