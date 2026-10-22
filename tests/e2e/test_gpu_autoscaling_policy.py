"""Offline safety contract for GPU scaling decisions; never calls cloud APIs."""
from dataclasses import replace
import unittest

from tools.autoscaling.policy import (
    ScalingObservation, ScalingPolicy, plan_scale,
)

NOW = 1_800_000_000


def config(**kw):
    values = dict(min_workers=0, max_workers=8, requests_per_worker=2,
                  worker_hourly_cost_cents=250, budget_hourly_cents=1000,
                  queue_wait_target_ms=3000, cooldown_seconds=90,
                  idle_to_min_seconds=600, max_observation_age_seconds=30)
    values.update(kw)
    return ScalingPolicy(**values)


def metrics(**kw):
    values = dict(observed_at_epoch_s=NOW, current_workers=2,
                  inflight_requests=4, queued_requests=0,
                  oldest_queue_wait_ms=0, last_scale_epoch_s=None,
                  idle_since_epoch_s=None)
    values.update(kw)
    return ScalingObservation(**values)


class AutoscalingPolicySafetyTests(unittest.TestCase):
    def decide(self, p=None, observation=None, now=NOW):
        return plan_scale(p or config(), observation or metrics(), now_epoch_s=now)

    def test_current_capacity_does_not_scale_without_backlog(self):
        result = self.decide()
        self.assertEqual((result.desired_workers, result.action),
                         (2, "hold"))
        self.assertFalse(result.executed)

    def test_burst_scales_with_bounded_capacity_and_predicted_cost(self):
        result = self.decide(observation=metrics(queued_requests=40))
        self.assertEqual((result.desired_workers, result.action),
                         (4, "scale_up"))
        self.assertEqual(result.estimated_hourly_cents, 1000)
        self.assertFalse(result.executed)

    def test_budget_caps_worker_growth_even_with_large_queue(self):
        result = self.decide(p=config(budget_hourly_cents=500),
                             observation=metrics(queued_requests=100))
        self.assertEqual(result.desired_workers, 2)
        self.assertEqual(result.reason, "budget_or_max_capacity")

    def test_high_latency_queue_prompts_at_least_one_more_worker(self):
        obs = metrics(inflight_requests=1, queued_requests=1,
                      oldest_queue_wait_ms=6000)
        result = self.decide(observation=obs)
        self.assertEqual(result.desired_workers, 3)

    def test_queue_age_below_threshold_does_not_overscale(self):
        obs = metrics(inflight_requests=3, queued_requests=1,
                      oldest_queue_wait_ms=2999)
        self.assertEqual(self.decide(observation=obs).desired_workers, 2)

    def test_boot_from_scale_to_zero_when_requests_waiting(self):
        obs = metrics(current_workers=0, inflight_requests=0,
                      queued_requests=1, oldest_queue_wait_ms=1)
        result = self.decide(observation=obs)
        self.assertEqual((result.desired_workers, result.action), (1, "scale_up"))

    def test_stale_telemetry_refuses_to_act(self):
        result = self.decide(observation=metrics(queued_requests=100),
                             now=NOW+31)
        self.assertEqual((result.action, result.reason), ("hold", "stale_telemetry_human_review"))

    def test_future_telemetry_refuses_to_act(self):
        result = self.decide(observation=metrics(observed_at_epoch_s=NOW+1))
        self.assertEqual((result.action, result.reason), ("hold", "clock_regression_human_review"))

    def test_worker_count_outside_configured_cap_requires_human_review(self):
        result = self.decide(observation=metrics(current_workers=9, queued_requests=99))
        self.assertEqual((result.desired_workers, result.reason),
                         (9, "worker_or_cost_drift_human_review"))

    def test_existing_spend_above_cap_does_not_auto_kill_workers(self):
        result = self.decide(p=config(budget_hourly_cents=250),
                             observation=metrics(current_workers=3))
        self.assertEqual((result.desired_workers, result.reason),
                         (3, "worker_or_cost_drift_human_review"))

    def test_cooldown_blocks_flapping(self):
        result = self.decide(observation=metrics(queued_requests=100,
                                                 last_scale_epoch_s=NOW-2))
        self.assertEqual((result.action, result.reason), ("hold", "cooldown"))

    def test_idle_without_observed_since_does_not_scale_to_zero(self):
        result = self.decide(observation=metrics(inflight_requests=0))
        self.assertEqual((result.action, result.reason), ("hold", "idle_window_not_attested"))

    def test_sustained_idle_scales_only_to_configured_minimum(self):
        result = self.decide(observation=metrics(
            inflight_requests=0, idle_since_epoch_s=NOW-601))
        self.assertEqual((result.desired_workers, result.action), (0, "scale_down"))
        warm = self.decide(observation=metrics(
            inflight_requests=0, idle_since_epoch_s=NOW-599))
        self.assertEqual(warm.action, "hold")
        keep_one = self.decide(
            p=config(min_workers=1),
            observation=metrics(inflight_requests=0, idle_since_epoch_s=NOW-601))
        self.assertEqual(keep_one.desired_workers, 1)

    def test_budget_must_fit_minimum_before_recommending_new_workers(self):
        result = self.decide(
            p=config(min_workers=2, budget_hourly_cents=250),
            observation=metrics(queued_requests=100))
        self.assertEqual(result.reason, "insufficient_budget_human_review")

    def test_invalid_negative_bool_and_overflows_rejected(self):
        for changes in ({"max_workers": 0}, {"min_workers": 9},
                        {"requests_per_worker": True},
                        {"budget_hourly_cents": 0},
                        {"worker_hourly_cost_cents": -1},
                        {"max_observation_age_seconds": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config(**changes)
        for changes in ({"current_workers": True},
                        {"queued_requests": -1},
                        {"oldest_queue_wait_ms": 1},
                        {"idle_since_epoch_s": NOW+1},
                        {"last_scale_epoch_s": NOW+1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                metrics(**changes)

    def test_impossible_idle_signal_and_queue_latency_rejected(self):
        with self.assertRaises(ValueError):
            metrics(idle_since_epoch_s=NOW-600)
        with self.assertRaises(ValueError):
            metrics(queued_requests=0, oldest_queue_wait_ms=5)

    def test_never_provisions_or_executes_cloud_resources(self):
        decisions = (
            self.decide(observation=metrics(queued_requests=100)),
            self.decide(observation=metrics(inflight_requests=0, idle_since_epoch_s=NOW-601)),
            self.decide(observation=metrics(queued_requests=0)),
        )
        self.assertTrue(all(result.executed is False for result in decisions))
        self.assertEqual([x.action for x in decisions],
                         ["scale_up", "scale_down", "hold"])


if __name__ == "__main__":
    unittest.main()
