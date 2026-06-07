"""Open-Meteo forecast parsing + WeatherController offline/online behavior."""

import datetime as _dt
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
    assert weather.weathercode_to_type(80) == "showers"
    assert weather.weathercode_to_type(82) == "showers"
    assert weather.weathercode_to_type(95) == "storm"
    assert weather.weathercode_to_type(99) == "storm"
    assert weather.weathercode_to_type(73) == "snow"


def test_classify_day_splits_dry_sky_by_cloud_cover():
    # Dry codes (0-3) are refined by the day's mean cloud cover.
    assert weather.classify_day(0, 10) == "sun"
    assert weather.classify_day(0, 30) == "few"
    assert weather.classify_day(1, 55) == "partly"
    assert weather.classify_day(0, 90) == "cloudy"
    assert weather.classify_day(3, 30) == "few"     # overcast code, but only 30% -> few
    # No cloud data -> code-only fallback.
    assert weather.classify_day(2, None) == "partly"
    # Precip / fog keep their code-based icon regardless of cloud cover.
    assert weather.classify_day(45, 10) == "cloudy"   # fog
    assert weather.classify_day(61, 10) == "rain"
    assert weather.classify_day(80, 10) == "showers"
    assert weather.classify_day(95, 10) == "storm"
    assert weather.classify_day(73, 10) == "snow"


def test_type_label_covers_every_type():
    codes = [0, 1, 2, 3, 45, 48, 51, 61, 67, 71, 75, 80, 82, 85, 95, 99]
    types = {weather.weathercode_to_type(c) for c in codes} | {"few"}
    missing = types - set(weather._TYPE_LABEL)
    assert not missing, f"missing labels: {missing}"


def test_parse_forecast_uses_hourly_cloud_cover():
    # Same overcast code each day, but hourly cloud cover differs -> different icons.
    payload = {
        "daily": {
            "time": ["2026-06-07", "2026-06-08", "2026-06-09"],
            "weathercode": [3, 3, 3],
            "temperature_2m_max": [20, 21, 22],
            "temperature_2m_min": [10, 11, 12],
        },
        "hourly": {
            "time": (["2026-06-07T00:00"] * 2 + ["2026-06-08T00:00"] * 2
                     + ["2026-06-09T00:00"] * 2),
            "cloudcover": [10, 10, 60, 60, 95, 95],  # means: 10, 60, 95
        },
    }
    assert [d.type for d in weather.parse_forecast(payload)] == ["sun", "partly", "cloudy"]


def test_parse_forecast_maps_fields():
    out = weather.parse_forecast(_PAYLOAD)
    assert [d.type for d in out] == ["partly", "sun", "rain"]
    assert (out[0].date, out[0].hi, out[0].lo) == ("2026-06-05", 18.4, 11.0)


def test_parse_forecast_handles_empty():
    assert weather.parse_forecast({}) == []
    assert weather.parse_forecast({"daily": {}}) == []


def test_forecast_context_labels_days_and_filters_past():
    today = _dt.date(2026, 6, 7)  # a Sunday
    fc = [
        weather.DailyForecast("2026-06-06", "sun", 25, 12),     # yesterday -> dropped
        weather.DailyForecast("2026-06-07", "rain", 19, 14),    # today
        weather.DailyForecast("2026-06-08", "partly", 24, 11),  # tomorrow (Mon)
        weather.DailyForecast("2026-06-09", "cloudy", 26, 17),  # Tuesday
    ]
    ctx = weather.forecast_context("Klosterneuburg", fc, today)
    assert "Klosterneuburg" in ctx
    assert "today is 2026-06-07" in ctx
    assert "today (2026-06-07): rain, high 19" in ctx
    assert "low 14" in ctx
    assert "tomorrow (2026-06-08): partly cloudy, high 24" in ctx
    assert "Tuesday (2026-06-09): mostly cloudy / overcast" in ctx
    assert "2026-06-06" not in ctx  # past day filtered out


def test_forecast_context_empty_when_no_usable_data():
    today = _dt.date(2026, 6, 7)
    assert weather.forecast_context("X", [], today) == ""
    # all entries in the past -> nothing to report
    past = [weather.DailyForecast("2026-06-01", "sun", 20, 10)]
    assert weather.forecast_context("X", past, today) == ""


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
