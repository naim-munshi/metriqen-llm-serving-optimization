# GPU autoscaling decision policy — offline engineering slice

Metriqen now includes `tools.autoscaling.policy.plan_scale`, a **deterministic,
provider-neutral recommendation function** for prospective GPU workers.
It is **not** GPU autoscaling in production: the policy does not connect to
Runpod, Modal, Kubernetes, a cloud API, or any internet service. It cannot
start/stop GPU workers or see genuine model/queue telemetry.

## Inputs

Operator-supplied, trusted `ScalingPolicy`:
- `min_workers` and `max_workers` (strictly validated bounded integer caps)
- `requests_per_worker` — an **assumed** in-flight slot capacity, to be
  measured separately on actual GPU hardware
- `worker_hourly_cost_cents` and `budget_hourly_cents` — **estimated**
  instantaneous worker cost ceiling. This is **not** provider-side cost
  enforcement, a monthly credit balance or bill protection.
- `queue_wait_target_ms`, `cooldown_seconds`, `idle_to_min_seconds`,
  `max_observation_age_seconds`.

A trusted, aggregate `ScalingObservation` supplies current workers,
in-flight/queued requests, oldest queue wait, observation time, last scale
time and the operator/telemetry-attested idle start time. It carries **no**
user ID, prompt, model response, access credential, PII or project key.

Example with **synthetic** telemetry (never use as real autoscale evidence):

```python
from tools.autoscaling.policy import (
    ScalingObservation, ScalingPolicy, plan_scale,
)

policy = ScalingPolicy(
    min_workers=0, max_workers=8, requests_per_worker=2,
    worker_hourly_cost_cents=250, budget_hourly_cents=1000,
)
observed = ScalingObservation(
    observed_at_epoch_s=1800000000,
    current_workers=2, inflight_requests=4, queued_requests=12,
    oldest_queue_wait_ms=3500,
)
decision = plan_scale(policy, observed, now_epoch_s=1800000000)
assert decision.action == "scale_up"
assert decision.desired_workers == 4
assert decision.estimated_hourly_cents == 1000
assert decision.executed is False
```

## Fail-closed behavior

- Invalid/bool/negative counts, contradictory idle state and timestamps,
  unapproved worker drift and bad budget configuration fail validation or
  produce `hold` with a human-review reason.
- An observation older than the allowed age cannot trigger scale-up/down.
- Maximum workers is bounded by both operator count cap and expected
  hourly spend. The policy **never** downscales unexpected live workers
  to reconcile a budget overrun without a human reviewing active requests.
- Cooldown stops rapid back-and-forth scaling after a reported change.
- No automatic downscale with any nonzero in-flight or queued demand.
  Scale-to-zero is *recommended* only after a proven idle interval.
- No provider actions are performed, even when action says
  `scale_up`/`scale_down`.

Tests (also in the existing CI end-to-end job):

```bash
python3 -m unittest tests.e2e.test_gpu_autoscaling_policy -v
```

## Next milestones before actual GPU deployment

1. Choose a provider and deploy a **private** GPU inference worker with
   version-pinned, license-approved model image and controlled memory/driver.
2. Provide measured worker-capacity, GPU warmup, real queue metrics and
   instrumentation with trustworthy timestamps.
3. Build a provider adapter implementing **idempotent reconcile**, signed
   service-to-service authentication, worker readiness/draining, clear
   rollback and ownership checks. Separate planning from action.
4. Configure provider-native hard caps/alerts/spending controls **in the
   provider account**. A local forecast can never guarantee a bill cap.
5. Add fail-closed, logged security/tenant budget acceptance tests; no
   unauthenticated public Ollama/Go route or developer Model Manager access.
6. Observe real scale-out/in, cold-start latency, TTFT/p95, throughput,
   model quality and GPU hourly charges from the approved GPU account.

This is tracked under
[GPU autoscaling #116](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/116).
It is deliberately separated from the **local-only** Metriqen portfolio v1
release gates and does not justify marketing “GPU autoscaling deployed.”
