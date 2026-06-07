"""DODI.build_system_prompt() appends the weather forecast when available."""

import datetime as _dt
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from main import DODI  # noqa: E402
from network import weather  # noqa: E402


class FakeWeather:
    def __init__(self, results):
        self._results = results

    def start_fetch(self, *a):
        pass

    def snapshot(self):
        return {"state": "done", "results": self._results, "revision": 1}


def _dodi(*, located=True, results=None):
    settings = {
        "weather": ({"lat": 48.305, "lon": 16.325, "location_name": "Klosterneuburg"}
                    if located else {}),
        "streaming": {"enabled": False, "active": "gemini"},
        "conversation": {"timeout_seconds": 10},
    }
    d = DODI(settings)
    d._persona = "You are Dodi."          # avoid persona file IO
    d._weather = FakeWeather(results or [])
    return d


def test_prompt_includes_weather_when_available():
    fc = [weather.DailyForecast(_dt.date.today().isoformat(), "rain", 19, 14)]
    prompt = _dodi(results=fc).build_system_prompt()
    assert "You are Dodi." in prompt
    assert "WEATHER FORECAST" in prompt
    assert "Klosterneuburg" in prompt
    assert "today (" in prompt


def test_prompt_is_just_persona_without_location():
    assert _dodi(located=False, results=[]).build_system_prompt() == "You are Dodi."


def test_prompt_is_just_persona_when_no_forecast_yet():
    assert _dodi(located=True, results=[]).build_system_prompt() == "You are Dodi."
