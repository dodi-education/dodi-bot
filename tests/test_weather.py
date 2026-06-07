"""Open-Meteo forecast parsing + WeatherController offline/online behavior."""

import sys
import time
import urllib.error
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from network import weather  # noqa: E402

_PAYLOAD = {"daily": {
    "time": ["2026-06-05", "2026-06-06", "2026-06-07"],
    "weathercode": [2, 0, 61],
    "temperature_2m_max": [18.4, 21.1, 15.0],
    "temperature_2m_min": [11.0, 12.2, 9.3],
}}


def test_weathercode_mapping():
    assert weather.weathercode_to_type(0) == "sun"
    assert weather.weathercode_to_type(1) == "sun"
    assert weather.weathercode_to_type(2) == "partly"
    assert weather.weathercode_to_type(3) == "cloudy"
    assert weather.weathercode_to_type(45) == "cloudy"
    assert weather.weathercode_to_type(61) == "rain"
    assert weather.weathercode_to_type(80) == "rain"
    assert weather.weathercode_to_type(95) == "rain"
    assert weather.weathercode_to_type(73) == "snow"


def test_parse_forecast_maps_fields():
    out = weather.parse_forecast(_PAYLOAD)
    assert [d.type for d in out] == ["partly", "sun", "rain"]
    assert (out[0].date, out[0].hi, out[0].lo) == ("2026-06-05", 18.4, 11.0)


def test_parse_forecast_handles_empty():
    assert weather.parse_forecast({}) == []
    assert weather.parse_forecast({"daily": {}}) == []


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _drain(ctrl, timeout=2.0):
    """Wait for the controller's worker thread to leave the 'fetching' state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ctrl.snapshot()["state"] != "fetching":
            return
        time.sleep(0.005)
    raise AssertionError("weather worker did not finish")


def test_controller_offline_when_no_ssid():
    ctrl = weather.WeatherController(clock=_Clock(), fetch=lambda lat, lon: [],
                                     online=lambda: None)
    ctrl.start_fetch(1.0, 2.0)
    _drain(ctrl)
    assert ctrl.snapshot()["state"] == "offline"


def test_controller_done_with_results():
    res = weather.parse_forecast(_PAYLOAD)
    ctrl = weather.WeatherController(clock=_Clock(), fetch=lambda lat, lon: res,
                                     online=lambda: "MyNet")
    ctrl.start_fetch(1.0, 2.0)
    _drain(ctrl)
    snap = ctrl.snapshot()
    assert snap["state"] == "done"
    assert len(snap["results"]) == 3


def test_controller_offline_on_fetch_error():
    def boom(lat, lon):
        raise urllib.error.URLError("no route to host")
    ctrl = weather.WeatherController(clock=_Clock(), fetch=boom, online=lambda: "MyNet")
    ctrl.start_fetch(1.0, 2.0)
    _drain(ctrl)
    assert ctrl.snapshot()["state"] == "offline"


def test_controller_caches_fresh_result():
    calls = []
    res = weather.parse_forecast(_PAYLOAD)

    def fetch(lat, lon):
        calls.append((lat, lon))
        return res

    ctrl = weather.WeatherController(clock=_Clock(), fetch=fetch, online=lambda: "Net")
    ctrl.start_fetch(1.0, 2.0)
    _drain(ctrl)
    ctrl.start_fetch(1.0, 2.0)   # same coords, still fresh -> no second fetch
    _drain(ctrl)
    assert len(calls) == 1
