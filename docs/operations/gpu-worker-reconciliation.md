# GPU worker fleet reconciliation contract (simulated, offline)

Metriqen now has a second **strictly offline** GPU autoscaling engineering
component, `tools.autoscaling.reconciler`. It turns the existing bounded
capacity recommendation into a **revision-bound intent** and verifies a
candidate state transition using a fake in-memory worker fleet.

**This does not create, stop, drain or access actual GPUs.** It is not a
Runpod/Modal/Kubernetes adapter, a distributed locking mechanism, actual
cost enforcement, or proof of inference scaling. It intentionally requires
`allow_simulation=True` even for the in-memory operation, and every result
has `cloud_resources_modified=False`.

## The intended future control-plane flow

```text
Trusted queue/load telemetry
       |
AutoscalingPolicy (bounded count, budget estimate, cooldown, idle guard)
       |
Revision-bound ScalingIntent (short expiry and deterministic ID)
       |
Revalidate current demand and generation
       |
[SIMULATOR ONLY] Compare-and-swap desired count
       |
[SIMULATOR ONLY] Explicit fake provider acknowledgement
       |
[Future] Authenticated provider adapter / GPU workers
```

This separates **desired** vs **observed** worker count and rejects a
second scaling instruction while the first is pending. The simulated
fleet generation increments once per valid intention, and the fake
acknowledgement can only accept the current desired count for that
generation. Duplicate/competing/expired submissions never increment the
generation again.

## Offline example (no network, no credentials)

```python
from tools.autoscaling.policy import ScalingObservation, ScalingPolicy
from tools.autoscaling.reconciler import (
    FleetSnapshot, MemoryFleetSimulator, propose_intent,
)

now = 1800000000  # synthetic timestamp
policy = ScalingPolicy(
    min_workers=0, max_workers=8, requests_per_worker=2,
    worker_hourly_cost_cents=250, budget_hourly_cents=1000,
)
fleet = FleetSnapshot(
    pool_id="synthetic-gpu-pool", generation=7,
    desired_workers=2, observed_workers=2,
    observed_at_epoch_s=now,
)
telemetry = ScalingObservation(
    observed_at_epoch_s=now, current_workers=2,
    inflight_requests=4, queued_requests=4,
    oldest_queue_wait_ms=4000,
)
intent = propose_intent(policy, fleet, telemetry, now_epoch_s=now)
assert intent is not None and intent.desired_workers == 4
simulator = MemoryFleetSimulator(fleet)
result = simulator.submit(
    intent, policy, telemetry, now_epoch_s=now,
    allow_simulation=True,
)
assert result.status == "simulated_submission"
assert result.fleet.desired_workers == 4
assert result.fleet.observed_workers == 2  # GPU capacity did NOT change
assert not result.cloud_resources_modified
```

## What is actually verified

- Per-pool name, generation, worker-count and timestamp validation
- Snapshot must agree with live aggregate worker observation; desired and
  observed counts must match before proposing a new action
- Stale/future telemetry, unapproved idle claims and incompatible budget
  force a `hold` or no intent
- Deterministic, expiry-bound intent ID is **not a cryptographic signature
  or an API credential** and is never suitable for authorizing cloud calls
- Simulation submission compares expected fleet generation and worker count
  atomically under a process-local lock; duplicate and concurrent submissions
  cannot mutate twice
- Scale-down with live in-flight work is refused; the current policy and
  queue pressure are recomputed at submission, so old plans cannot act
  after demand changes
- A completed simulated state change remains **pending** until an explicit
  same-generation fake provider acknowledgement
- The offline tests assert every result has
  `cloud_resources_modified=False`

## Remaining real-production implementation

Provider authentication/ownership; genuine GPU worker provisioning APIs;
idempotent and persistent remote desired-state updates; distributed
lease fencing; worker readiness/health; safe drain and termination;
trusted queue telemetry; strict project auth and quotas; provider billing
hard caps; signed artifact identity; region/cold-start tests; and real
TTFT/p95/throughput/cost evidence. None of these are covered by the
in-memory simulator or existing localhost Ollama source tests.

For details see
[worker sizing policy](gpu-autoscaling-policy.md),
[real GPU acceptance tracker #116](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/116),
and [public deployment boundaries](../security/public-deployment-boundaries.md).
