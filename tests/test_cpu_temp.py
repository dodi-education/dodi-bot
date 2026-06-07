"""CPU temperature reader + per-minute averaging monitor (injected clock/reader)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from sensors import cpu_temp  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_read_parses_millidegrees(monkeypatch, tmp_path):
    f = tmp_path / "temp"
    f.write_text("48123\n")
    monkeypatch.setattr(cpu_temp, "_THERMAL", f)
    assert cpu_temp.read_cpu_temp_c() == 48.123


def test_first_minute_uses_partial_running_average():
    clk = FakeClock()
    temps = iter([50.0, 52.0, 54.0])
    m = cpu_temp.CpuTempMonitor(reader=lambda: next(temps), clock=clk)
    m.sample()
    clk.t = 5
    m.sample()
    clk.t = 10
    m.sample()
    # Still inside minute one -> partial average of what's been collected.
    assert m.average() == (50.0 + 52.0 + 54.0) / 3


def test_publishes_previous_minute_average():
    clk = FakeClock()
    it = iter([50.0, 60.0, 80.0])
    m = cpu_temp.CpuTempMonitor(reader=lambda: next(it), clock=clk)
    m.sample()              # t=0  -> bucket [50]
    clk.t = 30
    m.sample()              # t=30 -> bucket [50, 60]
    clk.t = 65
    m.sample()              # crosses 60s -> publish mean(50,60)=55, new bucket [80]
    assert m.average() == 55.0   # the previous minute, not the new 80


def test_none_reading_is_skipped():
    clk = FakeClock()
    it = iter([None, 50.0])
    m = cpu_temp.CpuTempMonitor(reader=lambda: next(it), clock=clk)
    m.sample()
    assert m.average() is None
    clk.t = 5
    m.sample()
    assert m.average() == 50.0
