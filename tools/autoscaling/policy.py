"""Deterministic, offline, provider-neutral GPU worker scaling policy.

This module does not make network calls, allocate GPU resources, or claim that
a provider has scaled. Production requires authenticated provider adapters,
fresh trusted queue telemetry, billing enforcement, and real integration tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil


def _bounded_int(name: str, value: int, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")


@dataclass(frozen=True, slots=True)
class ScalingPolicy:
    """Operator-authorized bounds, expressed as integer cents and seconds."""

    min_workers: int
    max_workers: int
    requests_per_worker: int
    worker_hourly_cost_cents: int
    budget_hourly_cents: int
    queue_wait_target_ms: int = 3000
    cooldown_seconds: int = 90
    idle_to_min_seconds: int = 600
    max_observation_age_seconds: int = 30

    def __post_init__(self) -> None:
        _bounded_int("min_workers", self.min_workers, 0, 1000)
        _bounded_int("max_workers", self.max_workers, 1, 1000)
        if self.min_workers > self.max_workers:
            raise ValueError("minimum GPU workers exceeds maximum")
        _bounded_int("requests_per_worker", self.requests_per_worker, 1, 1024)
        _bounded_int("worker_hourly_cost_cents", self.worker_hourly_cost_cents, 1, 1_000_000)
        _bounded_int("budget_hourly_cents", self.budget_hourly_cents, 1, 1_000_000_000)
        _bounded_int("queue_wait_target_ms", self.queue_wait_target_ms, 1, 3_600_000)
        _bounded_int("cooldown_seconds", self.cooldown_seconds, 0, 86_400)
        _bounded_int("idle_to_min_seconds", self.idle_to_min_seconds, 60, 86_400)
        _bounded_int("max_observation_age_seconds",
                     self.max_observation_age_seconds, 1, 300)

    @property
    def cost_bounded_max(self) -> int:
        """Maximum predicted worker count; an estimate, not a billing limit."""
        return min(self.max_workers,
                   self.budget_hourly_cents // self.worker_hourly_cost_cents)


@dataclass(frozen=True, slots=True)
class ScalingObservation:
    """Trusted aggregate observations only: no prompts, users, or project IDs."""

    observed_at_epoch_s: int
    current_workers: int
    inflight_requests: int
    queued_requests: int
    oldest_queue_wait_ms: int
    last_scale_epoch_s: int | None = None
    idle_since_epoch_s: int | None = None

    def __post_init__(self) -> None:
        _bounded_int("observed_at_epoch_s", self.observed_at_epoch_s, 0, 4_102_444_800)
        _bounded_int("current_workers", self.current_workers, 0, 1000)
        _bounded_int("inflight_requests", self.inflight_requests, 0, 1_000_000)
        _bounded_int("queued_requests", self.queued_requests, 0, 1_000_000)
        _bounded_int("oldest_queue_wait_ms", self.oldest_queue_wait_ms, 0, 86_400_000)
        for name, value in (("last_scale_epoch_s", self.last_scale_epoch_s),
                            ("idle_since_epoch_s", self.idle_since_epoch_s)):
            if value is not None:
                _bounded_int(name, value, 0, 4_102_444_800)
                if value > self.observed_at_epoch_s:
                    raise ValueError(f"{name} cannot exceed observation time")
        if self.queued_requests == 0 and self.oldest_queue_wait_ms:
            raise ValueError("empty queue cannot have positive wait duration")
        if (self.inflight_requests or self.queued_requests) and self.idle_since_epoch_s is not None:
            raise ValueError("idle timestamp contradicts observed demand")


@dataclass(frozen=True, slots=True)
class ScalingDecision:
    """Desired worker count suggestion, not an executed provider action."""

    desired_workers: int
    action: str
    reason: str
    estimated_hourly_cents: int
    executed: bool = False


def plan_scale(policy: ScalingPolicy, observed: ScalingObservation,
               *, now_epoch_s: int) -> ScalingDecision:
    """Fail closed for stale/drifted telemetry, unapproved cost or cooldown."""
    _bounded_int("now_epoch_s", now_epoch_s, 0, 4_102_444_800)
    current = observed.current_workers

    def decide(target: int, reason: str) -> ScalingDecision:
        action = "scale_up" if target > current else ("scale_down" if target < current else "hold")
        return ScalingDecision(target, action, reason,
                               target * policy.worker_hourly_cost_cents)

    if now_epoch_s < observed.observed_at_epoch_s:
        return decide(current, "clock_regression_human_review")
    if now_epoch_s - observed.observed_at_epoch_s > policy.max_observation_age_seconds:
        return decide(current, "stale_telemetry_human_review")
    affordable = policy.cost_bounded_max
    if affordable < policy.min_workers:
        return decide(current, "insufficient_budget_human_review")
    if current > policy.max_workers or current < policy.min_workers or current > affordable:
        # Never automatically terminate unexpected live workers.
        return decide(current, "worker_or_cost_drift_human_review")
    if (observed.last_scale_epoch_s is not None
            and now_epoch_s - observed.last_scale_epoch_s < policy.cooldown_seconds):
        return decide(current, "cooldown")

    demand = observed.inflight_requests + observed.queued_requests
    if demand == 0:
        if current <= policy.min_workers:
            return decide(current, "at_minimum")
        if observed.idle_since_epoch_s is None:
            return decide(current, "idle_window_not_attested")
        if now_epoch_s - observed.idle_since_epoch_s < policy.idle_to_min_seconds:
            return decide(current, "idle_window")
        # Operator can set min_workers=0 to request scale-to-zero, but this is
        # only a recommendation; the provider adapter must drain workers.
        return decide(policy.min_workers, "sustained_idle")

    target = max(policy.min_workers, ceil(demand / policy.requests_per_worker))
    if (observed.queued_requests > 0 and current > 0
            and observed.oldest_queue_wait_ms >= policy.queue_wait_target_ms):
        target = max(target, current + 1)
    target = min(target, affordable)
    if target <= current:
        if affordable <= current and observed.queued_requests > 0:
            return decide(current, "budget_or_max_capacity")
        return decide(current, "current_capacity_sufficient")
    return decide(target, "bounded_queue_pressure")
