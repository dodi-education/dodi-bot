"""City geocoding via the Open-Meteo geocoding API (no API key required).

Resolves a free-text city query to name / country / lat / lon, which the Location
sheet writes into ``settings["weather"]``. Network calls run on a worker thread
(:class:`GeocodeController`) so the pygame render loop never blocks.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("DODI.network")

_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass
class Place:
    name: str
    country_code: str
    lat: float
    lon: float
    admin1: str = ""

    @property
    def subtitle(self) -> str:
        return f"{self.admin1}, {self.country_code}" if self.admin1 else self.country_code


def parse_results(payload: dict) -> list[Place]:
    out = []
    for r in (payload or {}).get("results", []) or []:
        try:
            out.append(Place(
                name=r["name"],
                country_code=r.get("country_code", "") or "",
                lat=float(r["latitude"]),
                lon=float(r["longitude"]),
                admin1=r.get("admin1", "") or "",
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def search(query: str, count: int = 8, timeout: float = 8.0) -> list[Place]:
    """Return up to ``count`` matching places for ``query`` (empty on error/blank)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    url = _ENDPOINT + "?" + urllib.parse.urlencode(
        {"name": q, "count": count, "language": "en", "format": "json"})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("geocode failed for %r: %s", q, e)
        return []
    return parse_results(payload)


class GeocodeController:
    """Runs searches on a worker thread; the UI polls :meth:`snapshot` each frame.

    A monotonically increasing request id guards against out-of-order results
    (a slow earlier query landing after a newer one).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "idle"          # 'idle' | 'searching' | 'done'
        self._query = ""
        self._results: list[Place] = []
        self._req = 0

    def start_search(self, query: str) -> None:
        with self._lock:
            self._req += 1
            req = self._req
            self._query = query
            self._state = "searching" if query.strip() else "idle"
            if not query.strip():
                self._results = []
                return
        threading.Thread(target=self._worker, args=(query, req), daemon=True).start()

    def _worker(self, query: str, req: int) -> None:
        places = search(query)
        with self._lock:
            if req != self._req:
                return  # a newer search superseded this one
            self._results = places
            self._state = "done"

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self._state, "query": self._query, "results": list(self._results)}
