"""Raspberry Pi CPU temperature sampling for the info screen.

Reads ``/sys/class/thermal/thermal_zone0/temp`` (millidegrees C) defensively —
mirroring the safe-sysfs pattern in :mod:`night_mode.display_power` — and keeps a
one-minute bucket so the info screen can show the **average of the previous
minute**, refreshed once a minute. Sampling runs on a daemon thread started at
boot (by :class:`display.avatar.AvatarDisplay`) so an average is ready before the
info screen is ever opened.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("DODI.sensors")

_THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")


def read_cpu_temp_c() -> float | None:
    """Current CPU temperature in °C, or ``None`` if it can't be read."""
    try:
        return int(_THERMAL.read_text().strip()) / 1000.0
    except Exception:
        try:  # gpiozero also reads sysfs, but works on boards with a different path
            from gpiozero import CPUTemperature
            return float(CPUTemperature().temperature)
        except Exception as e:
            logger.debug("CPU temp read failed: %s", e)
            return None


class CpuTempMonitor:
    """Samples CPU temperature on a daemon thread; exposes the previous-minute mean.

    Readings accumulate into the current minute's bucket. When a minute elapses
    the bucket's mean is *published* and a fresh bucket starts, so :meth:`average`
    returns a value that changes only once a minute (the previous minute's
    average). During the first minute after boot it returns the partial running
    average so the gauge isn't blank. The clock and reader are injectable for
    tests (no real sleeping required).
    """

    MINUTE = 60.0  # seconds per averaging window

    def __init__(self, sample_interval: float = 5.0, reader=read_cpu_temp_c,
                 clock=time.monotonic):
        self._interval = sample_interval
        self._reader = reader
        self._clock = clock
        self._lock = threading.Lock()
        self._bucket: list[float] = []
        self._minute_start: float | None = None
        self._published: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dodi-cputemp")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def sample(self) -> None:
        """Take one reading now and roll the minute bucket. Also used by tests."""
        t = self._reader()
        if t is None:
            return
        now = self._clock()
        with self._lock:
            if self._minute_start is None:
                self._minute_start = now
            if now - self._minute_start >= self.MINUTE:
                if self._bucket:
                    self._published = sum(self._bucket) / len(self._bucket)
                self._bucket = []
                self._minute_start = now
            self._bucket.append(t)

    def average(self) -> float | None:
        """Average of the previous completed minute (partial during minute one)."""
        with self._lock:
            if self._published is not None:
                return self._published
            if self._bucket:
                return sum(self._bucket) / len(self._bucket)
            return None

    def _run(self) -> None:
        self.sample()  # seed immediately so an average exists within seconds
        while not self._stop.wait(self._interval):
            self.sample()
