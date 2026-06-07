"""Open-Meteo geocoding parser tests (no network — canned JSON payload)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from network import geocode  # noqa: E402

_PAYLOAD = {
    "results": [
        {"name": "Berlin", "country_code": "DE", "latitude": 52.52, "longitude": 13.405,
         "admin1": "Berlin"},
        {"name": "Vienna", "country_code": "AT", "latitude": 48.21, "longitude": 16.37,
         "admin1": "Vienna"},
        {"name": "Broken"},  # missing lat/lon -> skipped
    ]
}


def test_parse_results_maps_fields_and_skips_bad():
    places = geocode.parse_results(_PAYLOAD)
    assert [p.name for p in places] == ["Berlin", "Vienna"]
    b = places[0]
    assert b.country_code == "DE"
    assert (b.lat, b.lon) == (52.52, 13.405)
    assert b.subtitle == "Berlin, DE"


def test_parse_results_empty_payload():
    assert geocode.parse_results({}) == []
    assert geocode.parse_results({"results": None}) == []


def test_subtitle_without_admin1():
    p = geocode.Place(name="X", country_code="FR", lat=1.0, lon=2.0)
    assert p.subtitle == "FR"


def test_search_blank_query_returns_empty_without_network():
    assert geocode.search("") == []
    assert geocode.search("a") == []   # < 2 chars, no request made


def test_controller_ignores_stale_results(monkeypatch):
    ctrl = geocode.GeocodeController()
    # Simulate an out-of-order worker: req 1 lands after req 2 bumped the counter.
    ctrl._req = 2
    ctrl._worker("oldquery", req=1)
    assert ctrl.snapshot()["results"] == []
