"""Private sampled AC-power evidence for operator-only Mac benchmark phases.

This records observed power states, not a claim of uninterrupted AC,
thermal stability, or independently authenticated hardware.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Callable


def read_power_source() -> str:
    """Return one bounded, non-sensitive source label; fail closed."""
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True,
            check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    if "Now drawing from 'AC Power'" in result.stdout:
        return "AC"
    if "Now drawing from 'Battery Power'" in result.stdout:
        return "Battery"
    return "unknown"


class PowerMonitor:
    """Sample through the sweep + quality; preserve a private report on failure."""

    def __init__(self, output: Path, *, interval_seconds: float = 5.0,
                 reader: Callable[[], str] = read_power_source):
        if interval_seconds <= 0:
            raise ValueError("positive power sampling interval required")
        self.output = output
        self.interval_seconds = interval_seconds
        self.reader = reader
        self.samples: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            source = self.reader()
        except Exception:
            source = "unknown"
        if source not in ("AC", "Battery"):
            source = "unknown"
        item = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "source": source,
        }
        with self._lock:
            self.samples.append(item)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    @property
    def initial_source(self) -> str:
        return self.samples[0]["source"] if self.samples else "unknown"

    @property
    def status(self) -> str:
        with self._lock:
            sources = [item["source"] for item in self.samples]
        return ("ac_samples_only" if len(sources) >= 2 and all(s == "AC" for s in sources)
                else "power_condition_not_maintained")

    def __enter__(self) -> "PowerMonitor":
        self._sample()  # Test immediately, before any model requests.
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._sample()  # Includes state after quality or a failed sweep.
        report = {
            "schema_version": 1,
            "kind": "local_mac_power_sampling",
            "status": self.status,
            "sampling_interval_seconds": self.interval_seconds,
            "samples": self.samples,
            "limitations": [
                "Discrete samples do not prove uninterrupted AC between samples",
                "Power samples alone do not measure thermal drift, GPU utilization or quality",
                "Original failure/partial benchmark files must remain private and intact",
            ],
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.output, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return False
