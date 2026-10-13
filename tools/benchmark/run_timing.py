"""Strict, additive validation for operator-side benchmark run timing.

Timing metadata is not signed and does not attest actual model execution.
Legacy reports without timing remain readable for historical comparison.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re

UTC_MILLIS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z\Z")
EXPECTED_KEYS = {"started_at_utc", "finished_at_utc", "elapsed_seconds", "scope"}


def parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or UTC_MILLIS.fullmatch(value) is None:
        raise ValueError("run timing must have an RFC3339 UTC millisecond timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("invalid run timing date/time") from None


def validate(timing: object, measured_wall_seconds: float) -> dict:
    if not isinstance(timing, dict) or set(timing) != EXPECTED_KEYS:
        raise ValueError("invalid run timing fields")
    if timing["scope"] != "warmup_and_measured_requests":
        raise ValueError("run timing scope is not comparable")
    elapsed = timing["elapsed_seconds"]
    if type(elapsed) not in (float, int) or not math.isfinite(elapsed) or not 0 < elapsed < 86400:
        raise ValueError("invalid run monotonic duration")
    if (type(measured_wall_seconds) not in (float, int)
            or not math.isfinite(measured_wall_seconds) or not 0 < measured_wall_seconds < 86400):
        raise ValueError("invalid measured wall duration")
    if elapsed + .01 < measured_wall_seconds:
        raise ValueError("run duration is shorter than the measured wall duration")
    start = parse_utc(timing["started_at_utc"])
    end = parse_utc(timing["finished_at_utc"])
    clock_seconds = (end - start).total_seconds()
    if clock_seconds < 0 or abs(clock_seconds - elapsed) > max(2.0, elapsed * .03):
        raise ValueError("run UTC clock duration disagrees with monotonic duration")
    return timing


def validate_sequence(entries: list[dict]) -> None:
    """Check order only when all reports have the new optional timing metadata."""
    presence = ["timing" in entry for entry in entries]
    if any(presence) and not all(presence):
        raise ValueError("mixed timed and legacy runs inside the same sweep")
    previous_end = None
    for entry in entries:
        if not presence or not any(presence):
            return
        timing = entry["timing"]
        if not isinstance(timing, dict):
            raise ValueError("invalid sweep run timing")
        start = parse_utc(timing.get("started_at_utc"))
        end = parse_utc(timing.get("finished_at_utc"))
        if previous_end is not None and start < previous_end:
            raise ValueError("run timestamps overlap or are out of order")
        previous_end = end
