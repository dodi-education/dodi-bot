"""Info screen: weather hint vs forecast, hint tap-through, CPU gauge (headless)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

pygame.init()
pygame.font.init()

from display.info_screen import InfoScreen  # noqa: E402
from network import weather  # noqa: E402

LOC = {"weather": {"lat": 48.30521, "lon": 16.32522, "location_name": "Klosterneuburg"}}


class FakeMonitor:
    def __init__(self, avg):
        self._avg = avg

    def average(self):
        return self._avg


class FakeAvatar:
    def __init__(self, settings, avg=52.0):
        self._settings = settings
        self._cpu_monitor = FakeMonitor(avg)
        self.opened = []

    def open_system_settings_at(self, section, sub=None):
        self.opened.append((section, sub))


class FakeWeather:
    def __init__(self, state, results):
        self._snap = {"state": state, "results": results, "revision": 1}
        self.fetches = 0

    def start_fetch(self, *a):
        self.fetches += 1

    def snapshot(self):
        return dict(self._snap)


def _draw(av, fw):
    scr = InfoScreen(av, weather=fw)
    scr.open()
    scr.draw(pygame.Surface((800, 800)))
    return scr


def _tap(scr, rect, dx=0, dy=0):
    cx, cy = rect.centerx / 800, rect.centery / 800
    scr.handle_event(pygame.event.Event(pygame.FINGERDOWN, x=cx, y=cy, finger_id=1, touch_id=0))
    scr.handle_event(pygame.event.Event(pygame.FINGERUP, x=cx + dx / 800, y=cy + dy / 800,
                                        finger_id=1, touch_id=0))


def test_no_location_shows_configure_hint():
    scr = _draw(FakeAvatar({"weather": {}}), FakeWeather("idle", []))
    assert "hint_weather" in scr._rects
    assert scr._hint_target == ("internet", "location")


def test_offline_shows_connect_hint():
    scr = _draw(FakeAvatar(LOC), FakeWeather("offline", []))
    assert scr._hint_target == ("internet", None)


def test_results_render_forecast_without_hint():
    res = [weather.DailyForecast("2026-06-05", "partly", 18, 11),
           weather.DailyForecast("2026-06-06", "sun", 21, 12),
           weather.DailyForecast("2026-06-07", "rain", 15, 9)]
    scr = _draw(FakeAvatar(LOC), FakeWeather("done", res))
    assert "hint_weather" not in scr._rects
    assert scr._hint_target is None


def test_stale_results_shown_even_when_offline():
    res = [weather.DailyForecast("2026-06-05", "sun", 20, 10)]
    scr = _draw(FakeAvatar(LOC), FakeWeather("offline", res))
    assert "hint_weather" not in scr._rects  # prefer showing the last good forecast


def test_tap_configure_hint_opens_location():
    av = FakeAvatar({"weather": {}})
    scr = _draw(av, FakeWeather("idle", []))
    _tap(scr, scr._rects["hint_weather"])
    assert av.opened == [("internet", "location")]


def test_tap_connect_hint_opens_internet():
    av = FakeAvatar(LOC)
    scr = _draw(av, FakeWeather("offline", []))
    _tap(scr, scr._rects["hint_weather"])
    assert av.opened == [("internet", None)]


def test_swipe_starting_on_hint_does_not_open_settings():
    av = FakeAvatar({"weather": {}})
    scr = _draw(av, FakeWeather("idle", []))
    _tap(scr, scr._rects["hint_weather"], dx=-220)  # release far away -> a swipe
    assert av.opened == []


def test_cpu_average_none_does_not_crash():
    scr = _draw(FakeAvatar(LOC, avg=None), FakeWeather("done", []))
    assert scr.cpu_round() is None


def test_sig_shape_is_stable():
    scr = _draw(FakeAvatar(LOC), FakeWeather("done", []))
    s = scr.sig()
    assert s[0] == "info" and len(s) == 5
