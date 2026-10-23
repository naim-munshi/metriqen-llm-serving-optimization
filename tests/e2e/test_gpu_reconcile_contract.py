"""Synthetic GPU fleet reconciliation: absolutely no cloud APIs or costs."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import unittest

from tools.autoscaling.policy import ScalingPolicy, ScalingObservation
from tools.autoscaling.reconciler import (
    FleetSnapshot, MemoryFleetSimulator, propose_intent,
)

NOW = 1_800_000_000


def policy(**changes):
    cfg = dict(min_workers=0, max_workers=8, requests_per_worker=2,
               worker_hourly_cost_cents=250, budget_hourly_cents=1000,
               cooldown_seconds=90, idle_to_min_seconds=600,
               max_observation_age_seconds=30)
    cfg.update(changes)
    return ScalingPolicy(**cfg)


def fleet(**changes):
    state = dict(pool_id="synthetic-gpu-pool", generation=7,
                 desired_workers=2, observed_workers=2,
                 observed_at_epoch_s=NOW)
    state.update(changes)
    return FleetSnapshot(**state)


def observed(**changes):
    sample = dict(observed_at_epoch_s=NOW, current_workers=2,
                  inflight_requests=4, queued_requests=4,
                  oldest_queue_wait_ms=4000)
    sample.update(changes)
    return ScalingObservation(**sample)


class ReconciliationSafetyTests(unittest.TestCase):
    def test_prepared_intent_has_no_secrets_and_never_acts(self):
        intent = propose_intent(policy(), fleet(), observed(), now_epoch_s=NOW)
        self.assertIsNotNone(intent)
        self.assertEqual(intent.desired_workers, 4)
        self.assertEqual(intent.expected_generation, 7)
        self.assertEqual(intent.estimated_hourly_cents, 1000)
        self.assertEqual(len(intent.intent_id), 64)
        self.assertEqual(intent.pool_id, "synthetic-gpu-pool")
        self.assertLessEqual(intent.expires_at_epoch_s, NOW + 30)
        self.assertFalse(hasattr(intent, "bearer_token"))
        self.assertFalse(hasattr(intent, "provider_url"))

    def test_requires_opt_in_for_even_simulated_state_change(self):
        state = fleet()
        simulator = MemoryFleetSimulator(state)
        intent = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        blocked = simulator.submit(intent, policy(), observed(), now_epoch_s=NOW)
        self.assertEqual(blocked.status, "simulation_not_authorized")
        self.assertEqual(simulator.snapshot(), state)
        self.assertFalse(blocked.cloud_resources_modified)

    def test_one_simulated_submission_then_explicit_ack(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        first = sim.submit(plan, policy(), observed(),
                           now_epoch_s=NOW, allow_simulation=True)
        self.assertEqual(first.status, "simulated_submission")
        self.assertEqual((first.fleet.desired_workers,
                          first.fleet.observed_workers,
                          first.fleet.generation), (4, 2, 8))
        self.assertFalse(first.cloud_resources_modified)
        before_ack = sim.simulate_provider_ack(expected_generation=7,
                                               observed_workers=4)
        self.assertEqual(before_ack.status, "ack_conflict")
        wrong_count = sim.simulate_provider_ack(expected_generation=8,
                                                observed_workers=3)
        self.assertEqual(wrong_count.status, "ack_conflict")
        accepted = sim.simulate_provider_ack(expected_generation=8,
                                             observed_workers=4)
        self.assertEqual(accepted.status, "simulated_ack")
        self.assertEqual(accepted.fleet.observed_workers, 4)
        self.assertFalse(accepted.cloud_resources_modified)

    def test_exact_duplicate_intent_is_a_noop(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        first = sim.submit(plan, policy(), observed(),
                           now_epoch_s=NOW, allow_simulation=True)
        duplicate = sim.submit(plan, policy(), observed(),
                               now_epoch_s=NOW, allow_simulation=True)
        self.assertEqual(first.status, "simulated_submission")
        self.assertEqual(duplicate.status, "duplicate_intent")
        self.assertEqual(first.fleet, duplicate.fleet)

    def test_competing_threads_cannot_submit_same_generation_twice(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        def submit(_):
            return sim.submit(plan, policy(), observed(),
                              now_epoch_s=NOW, allow_simulation=True).status
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(submit, range(16)))
        self.assertEqual(statuses.count("simulated_submission"), 1)
        self.assertEqual(statuses.count("duplicate_intent"), 15)
        self.assertEqual(sim.snapshot().generation, 8)

    def test_mismatched_fleet_and_pending_transition_never_plan(self):
        for f, telemetry in (
            (fleet(desired_workers=3), observed()),
            (fleet(observed_workers=1), observed()),
            (fleet(observed_at_epoch_s=NOW-31), observed()),
            (fleet(observed_at_epoch_s=NOW+1), observed()),
            (fleet(), observed(observed_at_epoch_s=NOW-31)),
        ):
            with self.subTest(fleet=f):
                self.assertIsNone(
                    propose_intent(policy(), f, telemetry, now_epoch_s=NOW)
                )

    def test_expired_intent_never_changes_state(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        response = sim.submit(plan, policy(), observed(),
                              now_epoch_s=plan.expires_at_epoch_s,
                              allow_simulation=True)
        self.assertEqual(response.status, "expired_or_stale_telemetry")
        self.assertEqual(sim.snapshot(), state)

    def test_changed_live_queue_denies_previous_plan(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        changed = observed(inflight_requests=4, queued_requests=0,
                           oldest_queue_wait_ms=0)
        result = sim.submit(plan, policy(), changed, now_epoch_s=NOW,
                            allow_simulation=True)
        self.assertEqual(result.status, "live_demand_changed")
        self.assertEqual(sim.snapshot(), state)

    def test_modified_budget_blocks_original_intent(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        result = sim.submit(plan, policy(budget_hourly_cents=500),
                            observed(), now_epoch_s=NOW, allow_simulation=True)
        self.assertEqual(result.status, "budget_or_policy_conflict")
        self.assertEqual(sim.snapshot(), state)

    def test_wrong_pool_or_old_generation_rejected(self):
        state = fleet()
        sim = MemoryFleetSimulator(state)
        plan = propose_intent(policy(), state, observed(), now_epoch_s=NOW)
        for different in (replace(plan, pool_id="other-pool"),
                          replace(plan, expected_generation=6),
                          replace(plan, expected_observed_workers=0)):
            result = sim.submit(different, policy(), observed(),
                                now_epoch_s=NOW, allow_simulation=True)
            self.assertEqual(result.status, "generation_or_fleet_conflict")
        self.assertEqual(sim.snapshot(), state)

    def test_scale_to_zero_requires_confirmed_idle_and_current_empty(self):
        state = fleet()
        idle = observed(inflight_requests=0, queued_requests=0,
                        oldest_queue_wait_ms=0, idle_since_epoch_s=NOW-700)
        intent = propose_intent(policy(), state, idle, now_epoch_s=NOW)
        self.assertEqual(intent.desired_workers, 0)
        sim = MemoryFleetSimulator(state)
        busy = observed(inflight_requests=1, queued_requests=0,
                        oldest_queue_wait_ms=0)
        rejected = sim.submit(intent, policy(), busy, now_epoch_s=NOW,
                              allow_simulation=True)
        self.assertEqual(rejected.status, "live_demand_changed")
        accepted = sim.submit(intent, policy(), idle,
                              now_epoch_s=NOW, allow_simulation=True)
        self.assertEqual(accepted.status, "simulated_submission")
        self.assertEqual(accepted.fleet.desired_workers, 0)
        self.assertEqual(accepted.fleet.observed_workers, 2)
        self.assertFalse(accepted.cloud_resources_modified)

    def test_hold_and_insufficient_budget_produce_no_intents(self):
        no_queue = observed(queued_requests=0, oldest_queue_wait_ms=0)
        self.assertIsNone(propose_intent(policy(), fleet(), no_queue,
                                         now_epoch_s=NOW))
        self.assertIsNone(propose_intent(policy(budget_hourly_cents=250),
                                         fleet(), observed(), now_epoch_s=NOW))
        cooldown = observed(last_scale_epoch_s=NOW - 5)
        self.assertIsNone(propose_intent(policy(), fleet(), cooldown,
                                         now_epoch_s=NOW))

    def test_illegal_identifiers_and_invalid_timestamps_rejected(self):
        for bad in ("a", "UPPER", "user@example.com", "abc/", "", "a"*65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fleet(pool_id=bad)
        with self.assertRaises(ValueError):
            fleet(generation=True)
        with self.assertRaises(ValueError):
            fleet(desired_workers=-1)

    def test_false_cloud_provisioning_claim_never_appears(self):
        sim = MemoryFleetSimulator(fleet())
        intent = propose_intent(policy(), fleet(), observed(), now_epoch_s=NOW)
        for response in (
            sim.submit(intent, policy(), observed(),
                       now_epoch_s=NOW, allow_simulation=True),
            sim.submit(intent, policy(), observed(),
                       now_epoch_s=NOW, allow_simulation=True),
            sim.simulate_provider_ack(expected_generation=8, observed_workers=4),
        ):
            self.assertFalse(response.cloud_resources_modified)


if __name__ == "__main__":
    unittest.main()
