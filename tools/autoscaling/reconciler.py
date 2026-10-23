"""Offline, revision-fenced GPU scaling intent and fake-fleet reconciliation.

This is a simulator and contract test seam ONLY. It cannot provision GPUs,
send provider API calls, persist distributed leases or guarantee cloud costs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from threading import Lock

from tools.autoscaling.policy import (
    ScalingObservation, ScalingPolicy, _bounded_int, plan_scale,
)

POOL_ID = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")
INTENT_ID = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True, slots=True)
class FleetSnapshot:
    pool_id: str
    generation: int
    desired_workers: int
    observed_workers: int
    observed_at_epoch_s: int

    def __post_init__(self) -> None:
        if not isinstance(self.pool_id, str) or not POOL_ID.fullmatch(self.pool_id):
            raise ValueError("invalid local fleet identifier")
        _bounded_int("generation", self.generation, 0, 2_000_000_000)
        _bounded_int("desired_workers", self.desired_workers, 0, 1000)
        _bounded_int("observed_workers", self.observed_workers, 0, 1000)
        _bounded_int("observed_at_epoch_s", self.observed_at_epoch_s, 0, 4_102_444_800)


@dataclass(frozen=True, slots=True)
class ScalingIntent:
    """A non-authoritative proposal, not a bearer token or signed instruction."""

    intent_id: str
    pool_id: str
    expected_generation: int
    expected_observed_workers: int
    desired_workers: int
    expires_at_epoch_s: int
    reason: str
    estimated_hourly_cents: int

    def __post_init__(self) -> None:
        if not isinstance(self.intent_id, str) or not INTENT_ID.fullmatch(self.intent_id):
            raise ValueError("invalid planning intent")
        if not isinstance(self.pool_id, str) or not POOL_ID.fullmatch(self.pool_id):
            raise ValueError("invalid local fleet identifier")
        for name in ("expected_generation", "expected_observed_workers",
                     "desired_workers", "expires_at_epoch_s", "estimated_hourly_cents"):
            _bounded_int(name, getattr(self, name), 0, 4_102_444_800)
        if self.reason not in ("bounded_queue_pressure", "sustained_idle"):
            raise ValueError("unapproved reason for simulated scaling")


def propose_intent(policy: ScalingPolicy, fleet: FleetSnapshot,
                   observation: ScalingObservation, *, now_epoch_s: int
                   ) -> ScalingIntent | None:
    """Return a bounded intent only for a settled, fresh, consistent fleet."""
    _bounded_int("now_epoch_s", now_epoch_s, 0, 4_102_444_800)
    if (fleet.desired_workers != fleet.observed_workers
            or fleet.observed_workers != observation.current_workers):
        return None  # Pending capacity update or mismatched sources.
    if (fleet.observed_at_epoch_s > now_epoch_s
            or now_epoch_s - fleet.observed_at_epoch_s > policy.max_observation_age_seconds):
        return None
    decision = plan_scale(policy, observation, now_epoch_s=now_epoch_s)
    if decision.action == "hold" or decision.executed:
        return None
    if decision.desired_workers > policy.cost_bounded_max:
        return None
    if decision.action == "scale_down" and (
            observation.inflight_requests or observation.queued_requests):
        return None
    expires = min(now_epoch_s + 30,
                  observation.observed_at_epoch_s + policy.max_observation_age_seconds)
    if expires <= now_epoch_s:
        return None
    fields = {
        "pool": fleet.pool_id,
        "generation": fleet.generation,
        "observed": fleet.observed_workers,
        "target": decision.desired_workers,
        "issued": now_epoch_s,
        "expires": expires,
        "reason": decision.reason,
    }
    digest = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ScalingIntent(
        digest, fleet.pool_id, fleet.generation, fleet.observed_workers,
        decision.desired_workers, expires, decision.reason,
        decision.estimated_hourly_cents,
    )


@dataclass(frozen=True, slots=True)
class SimulationResult:
    status: str
    fleet: FleetSnapshot
    cloud_resources_modified: bool = False


class MemoryFleetSimulator:
    """Stateful fake with CAS; never a deployable/distributed GPU backend."""

    def __init__(self, initial: FleetSnapshot):
        self._lock = Lock()
        self._state = initial
        self._seen: set[str] = set()

    def snapshot(self) -> FleetSnapshot:
        with self._lock:
            return self._state

    def submit(self, intent: ScalingIntent, policy: ScalingPolicy,
               telemetry: ScalingObservation, *, now_epoch_s: int,
               allow_simulation: bool = False) -> SimulationResult:
        _bounded_int("now_epoch_s", now_epoch_s, 0, 4_102_444_800)
        with self._lock:
            current = self._state
            def fail(reason: str) -> SimulationResult:
                return SimulationResult(reason, current)

            if not allow_simulation:
                return fail("simulation_not_authorized")
            if intent.intent_id in self._seen:
                return fail("duplicate_intent")
            if (intent.pool_id != current.pool_id
                    or intent.expected_generation != current.generation
                    or intent.expected_observed_workers != current.observed_workers
                    or current.desired_workers != current.observed_workers):
                return fail("generation_or_fleet_conflict")
            if (now_epoch_s >= intent.expires_at_epoch_s
                    or now_epoch_s < telemetry.observed_at_epoch_s
                    or now_epoch_s - telemetry.observed_at_epoch_s >
                        policy.max_observation_age_seconds
                    or current.observed_workers != telemetry.current_workers):
                return fail("expired_or_stale_telemetry")
            if (intent.desired_workers < policy.min_workers
                    or intent.desired_workers > policy.cost_bounded_max
                    or intent.estimated_hourly_cents !=
                        intent.desired_workers * policy.worker_hourly_cost_cents):
                return fail("budget_or_policy_conflict")
            if intent.desired_workers == current.observed_workers:
                return fail("unchanged_target")
            if (intent.desired_workers < current.observed_workers
                    and (telemetry.inflight_requests or telemetry.queued_requests)):
                return fail("active_work_cannot_drain")
            updated = replace(
                current, generation=current.generation + 1,
                desired_workers=intent.desired_workers,
                observed_at_epoch_s=now_epoch_s,
            )
            self._state = updated
            self._seen.add(intent.intent_id)
            return SimulationResult("simulated_submission", updated)

    def simulate_provider_ack(self, *, expected_generation: int,
                              observed_workers: int) -> SimulationResult:
        """Fake capacity converges only on explicit acknowledged generation."""
        _bounded_int("expected_generation", expected_generation, 0, 2_000_000_000)
        _bounded_int("observed_workers", observed_workers, 0, 1000)
        with self._lock:
            if (self._state.generation != expected_generation
                    or observed_workers != self._state.desired_workers):
                return SimulationResult("ack_conflict", self._state)
            self._state = replace(self._state, observed_workers=observed_workers)
            return SimulationResult("simulated_ack", self._state)
