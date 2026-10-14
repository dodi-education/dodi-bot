"""3-day weather forecast via the Open-Meteo forecast API (no API key required).

Mirrors :mod:`network.geocode`: a blocking ``urllib`` fetch plus a threaded
controller (:class:`WeatherController`) so the pygame render loop never blocks.
The info screen reads ``settings["weather"]`` (lat/lon, written by the Location
sheet) and renders the result; WMO weather codes are mapped to the icon families
the design ships (sun / partly / cloudy / rain, plus snow).

Connectivity is checked via :func:`network.connectivity.has_default_route` on the
worker thread (throttled) — any interface counts, Ethernet as well as Wi-Fi — so a
missing internet connection yields a distinct ``"offline"`` state; the info screen
turns that into the "Connect to internet…" hint.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from network import connectivity

logger = logging.getLogger("DODI.network")

_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

# A descriptive UA is friendlier to the API/CDN than the default "Python-urllib"
# (which some edges rate-limit or stall — a contributor to read timeouts).
_USER_AGENT = "DODI-weather/1.0 (Raspberry Pi companion; open-meteo client)"


@dataclass
class DailyForecast:
    date: str       # ISO "YYYY-MM-DD"
    type: str       # sun | few | partly | cloudy | rain | showers | snow | storm
    hi: float       # daily max °C
    lo: float       # daily min °C


def weathercode_to_type(code: int) -> str:
    """Map an Open-Meteo WMO ``weathercode`` to an icon family (code-only fallback).

    The dry-sky split (sun/few/partly/cloudy) is refined by the day's mean cloud
    cover in :func:`classify_day`; this is what's used when no cloud data exists.
    """
    c = int(code)
    if c in (0, 1):            # clear / mainly clear
        return "sun"
    if c == 2:                 # partly cloudy
        return "partly"
    if c in (3, 45, 48):       # overcast / fog
        return "cloudy"
    if c in (71, 73, 75, 77, 85, 86):  # snow / snow showers
        return "snow"
    if c in (95, 96, 99):      # thunderstorm (+ with hail)
        return "storm"
    if c in (80, 81, 82):      # rain showers
        return "showers"
    return "rain"              # 51-57 drizzle, 61-67 rain / freezing rain


def classify_day(code: int, cloud_mean: float | None) -> str:
    """Icon type combining the WMO code with the day's mean cloud cover (%).

    Precipitation, snow, storms, showers and fog keep their code-based icon; the
    dry sky (clear/partly/overcast codes 0-3) is split four ways by ``cloud_mean``
    so a day that's only partly cloudy doesn't get a full cloud. Falls back to the
    code-only mapping when cloud cover is unavailable.
    """
    c = int(code)
    base = weathercode_to_type(c)
    if base in ("sun", "partly", "cloudy") and c not in (45, 48) and cloud_mean is not None:
        # Okta-style bands for mean daily cloud cover.
        if cloud_mean < 15:
            return "sun"
        if cloud_mean < 40:
            return "few"
        if cloud_mean < 70:
            return "partly"
        return "cloudy"
    return base


# Condition family -> natural-language label for the AI context block. The model
# translates these into the user's language when answering.
_TYPE_LABEL = {
    "sun": "clear and sunny",
    "few": "mostly sunny",
    "partly": "partly cloudy",
    "cloudy": "mostly cloudy / overcast",
    "rain": "rain",
    "showers": "rain showers",
    "snow": "snow",
    "storm": "thunderstorms",
}


def _hourly_cloud_by_day(hourly: dict) -> dict:
    """Mean cloud cover (%) per ISO date from Open-Meteo hourly ``cloudcover``."""
    times = (hourly or {}).get("time") or []
    cover = (hourly or {}).get("cloudcover") or (hourly or {}).get("cloud_cover") or []
    sums: dict = {}
    counts: dict = {}
    for t, c in zip(times, cover):
        if c is None:
            continue
        day = str(t)[:10]
        sums[day] = sums.get(day, 0.0) + c
        counts[day] = counts.get(day, 0) + 1
    return {d: sums[d] / counts[d] for d in sums if counts[d]}

_WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _relative_day(date_iso: str, today: _dt.date) -> str:
    """``today`` / ``tomorrow`` / weekday name for an ISO date relative to ``today``."""
    try:
        d = _dt.date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return date_iso
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    return _WEEKDAY[d.weekday()]


def current_days(forecasts, today: _dt.date) -> list[DailyForecast]:
    """``forecasts`` without past days (date < ``today``).

    A cached forecast outlives its dates when fetches stop succeeding (offline for
    days) or the cache crosses midnight; consumers use this so they never present
    yesterday's forecast as today's.
    """
    out = []
    for d in forecasts or []:
        try:
            if _dt.date.fromisoformat(d.date) < today:
                continue
        except (ValueError, TypeError):
            pass
        out.append(d)
    return out


def forecast_context(location_name: str, forecasts, today: _dt.date) -> str:
    """A short weather block for the AI system prompt (``""`` if no usable data).

    Days are labelled today/tomorrow/weekday so the model can answer questions
    like "Wie wird das Wetter heute/morgen?". Past days are skipped (see
    :func:`current_days`). Temperatures are °C.
    """
    rows = []
    for d in current_days(forecasts, today):
        label = _TYPE_LABEL.get(d.type, d.type)
        rows.append(f"- {_relative_day(d.date, today)} ({d.date}): {label}, "
                    f"high {round(d.hi)}°C, low {round(d.lo)}°C")
    if not rows:
        return ""
    header = (
        f"CURRENT WEATHER FORECAST for {location_name} (today is {today.isoformat()}). "
        "Use this data to answer the user's questions about the weather; temperatures "
        "are in degrees Celsius. If asked about a day not listed, say you only have "
        "the forecast for the next few days."
    )
    return header + "\n" + "\n".join(rows)


def parse_forecast(payload: dict) -> list[DailyForecast]:
    daily = (payload or {}).get("daily") or {}
    cloud_by_day = _hourly_cloud_by_day((payload or {}).get("hourly") or {})
    dates = daily.get("time") or []
    codes = daily.get("weathercode") or daily.get("weather_code") or []
    his = daily.get("temperature_2m_max") or []
    los = daily.get("temperature_2m_min") or []
    out: list[DailyForecast] = []
    for i, d in enumerate(dates):
        try:
            out.append(DailyForecast(
                date=d,
                type=classify_day(codes[i], cloud_by_day.get(d)),
                hi=float(his[i]),
                lo=float(los[i]),
            ))
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    return out


def fetch_forecast(lat: float, lon: float, days: int = 3, timeout: float = 30.0) -> list[DailyForecast]:
    """Fetch ``days`` of daily forecast for (lat, lon). Raises on network error.

    The timeout is deliberately generous: the free api.open-meteo.com endpoint
    can take 10s+ to respond (server-side, not network), and this runs on a
    background thread every 15 minutes, so waiting is cheap and avoids spurious
    "read operation timed out" failures.
    """
    url = _ENDPOINT + "?" + urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "daily": "weathercode,temperature_2m_max,temperature_2m_min",
        "hourly": "cloudcover",
        "timezone": "auto",
        "forecast_days": days,
    })
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_forecast(payload)


class WeatherController:
    """Fetches the forecast on a worker thread; the UI polls :meth:`snapshot`.

    A successful result is cached per rounded (lat, lon) for :attr:`REFRESH`
    seconds, so :meth:`start_fetch` (called per-frame by the info screen) is cheap
    and never fetches per-frame. The control loop drives the regular cadence with
    ``force=True`` every 15 minutes; the alarm forces one just before it rings.
    The connectivity check is throttled to :attr:`ONLINE_TTL`.
    ``state`` is one of ``idle`` / ``fetching`` / ``done`` / ``offline``;
    ``revision`` bumps on every state change so the render loop's repaint
    signature notices new results.
    """

    REFRESH = 15 * 60.0     # re-fetch a fresh location at most this often
    ONLINE_TTL = 30.0       # cache the connectivity check
    FETCH_ATTEMPTS = 2      # one retry on a transient failure (e.g. read timeout)
    RETRY_BACKOFF = 2.0     # seconds to wait between attempts

    def __init__(self, clock=time.monotonic, fetch=fetch_forecast,
                 online=connectivity.has_default_route, sleep=time.sleep):
        self._lock = threading.Lock()
        self._clock = clock
        self._fetch = fetch
        self._online = online
        self._sleep = sleep
        self._state = "idle"
        self._results: list[DailyForecast] = []
        self._revision = 0
        self._key: tuple | None = None
        self._fetched_at: float | None = None
        self._req = 0
        self._online_cache = False
        self._online_checked_at: float | None = None

    def _is_online(self) -> bool:
        now = self._clock()
        if self._online_checked_at is None or now - self._online_checked_at >= self.ONLINE_TTL:
            try:
                self._online_cache = bool(self._online())
            except Exception:
                self._online_cache = False
            self._online_checked_at = now
        return self._online_cache

    def start_fetch(self, lat: float, lon: float, force: bool = False) -> None:
        """Kick a background fetch. ``force`` bypasses the freshness cache so the
        control loop's 15-min cadence and the pre-alarm refresh always re-fetch."""
        key = (round(float(lat), 4), round(float(lon), 4))
        now = self._clock()
        with self._lock:
            if self._state == "fetching":
                return
            if not force:
                fresh = (self._state == "done" and self._key == key
                         and self._fetched_at is not None
                         and now - self._fetched_at < self.REFRESH)
                if fresh:
                    return
            self._req += 1
            req = self._req
            self._state = "fetching"
            self._key = key
        threading.Thread(target=self._worker, args=(key, req), daemon=True).start()

    def _worker(self, key: tuple, req: int) -> None:
        online = self._is_online()
        results = None
        if online:
            for attempt in range(1, self.FETCH_ATTEMPTS + 1):
                try:
                    results = self._fetch(key[0], key[1])
                    break
                except Exception as e:
                    if attempt < self.FETCH_ATTEMPTS:
                        logger.info("weather fetch attempt %d/%d failed: %s; retrying",
                                    attempt, self.FETCH_ATTEMPTS, e)
                        self._sleep(self.RETRY_BACKOFF)
                    else:
                        logger.warning("weather fetch failed after %d attempts: %s",
                                       self.FETCH_ATTEMPTS, e)
        with self._lock:
            if req != self._req:
                return  # a newer fetch superseded this one
            if results is not None:
                self._results = results
                self._state = "done"
                self._fetched_at = self._clock()
                logger.info("weather updated: %d day(s) for %s", len(results), key)
            elif not online:
                self._state = "offline"
                self._fetched_at = None
                logger.info("weather fetch skipped: offline")
            else:
                # online but the request failed: keep any prior forecast, allow a retry
                self._state = "done" if self._results else "offline"
                self._fetched_at = None
            self._revision += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self._state, "results": list(self._results),
                    "revision": self._revision}
