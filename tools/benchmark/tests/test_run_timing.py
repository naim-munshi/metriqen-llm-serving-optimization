"""UTC run-timing regression tests, synthetic and network-free."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tools.benchmark import run_timing


BASE = datetime(2026, 10, 14, tzinfo=timezone.utc)


def record(start=BASE, duration=10.3):
    end = start + timedelta(seconds=duration)
    return {
        "started_at_utc": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "finished_at_utc": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "elapsed_seconds": duration,
        "scope": "warmup_and_measured_requests",
    }


class RunTimingTests(unittest.TestCase):
    def test_valid_timing_includes_warmup_and_measured_duration(self):
        self.assertEqual(run_timing.validate(record(), 10.0), record())

    def test_invalid_missing_or_extra_fields_rejected(self):
        for payload in ({}, {"started_at_utc": BASE.isoformat()}, dict(record(), secret="don't store")):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                run_timing.validate(payload, 10)

    def test_reject_non_utc_timezone_and_bad_calendar_date(self):
        for wrong in ("2026-10-14T00:00:00+09:00", "2026-02-30T00:00:00.000Z",
                      "2026-10-14T00:00:00Z", "2026-10-14T00:00:00.123456Z"):
            payload = record()
            payload["started_at_utc"] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                run_timing.validate(payload, 10)

    def test_reject_clock_reversal_and_duration_mismatch(self):
        invalid = record()
        invalid["finished_at_utc"] = "2026-10-13T23:59:59.000Z"
        with self.assertRaises(ValueError):
            run_timing.validate(invalid, 10)
        invalid = record()
        invalid["finished_at_utc"] = "2026-10-14T00:00:20.300Z"
        with self.assertRaises(ValueError):
            run_timing.validate(invalid, 10)

    def test_reject_run_shorter_than_measured(self):
        with self.assertRaisesRegex(ValueError, "shorter"):
            run_timing.validate(record(duration=9), 10)

    def test_reject_nonfinite_negative_and_boolean_durations(self):
        for duration in (float("nan"), float("inf"), -1, 0, True):
            invalid = record()
            invalid["elapsed_seconds"] = duration
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                run_timing.validate(invalid, 5)

    def test_require_timing_scope(self):
        invalid = record()
        invalid["scope"] = "measured_only"
        with self.assertRaises(ValueError):
            run_timing.validate(invalid, 5)

    def test_ordered_runs_pass(self):
        runs = [{"timing": record(BASE + timedelta(seconds=i * 20))}
                for i in range(9)]
        run_timing.validate_sequence(runs)

    def test_overlapping_run_timing_rejected(self):
        runs = [{"timing": record(BASE)}, {"timing": record(BASE + timedelta(seconds=9))}]
        with self.assertRaisesRegex(ValueError, "overlap"):
            run_timing.validate_sequence(runs)

    def test_legacy_runs_allowed_but_mixed_runs_rejected(self):
        run_timing.validate_sequence([{}, {}, {}])
        with self.assertRaisesRegex(ValueError, "mixed"):
            run_timing.validate_sequence([{}, {"timing": record()}])


if __name__ == "__main__":
    unittest.main()
