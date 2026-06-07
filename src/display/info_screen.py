"""Info screen — the single-finger left-swipe "home" view.

A peer :class:`~display.avatar.DisplayMode` (not a modal overlay), so Dodi keeps
listening while it's shown. Reproduces the Claude Design "Dodi-Bot Home" mock for
the round 800x800 screen: a 560x380 widget grid (3 cols x 2 rows) holding three
tiles —

  * date + time      (top, spans 2 cols)
  * Dodi CPU temp     (top-right, a 270° arc gauge fed by the boot-time sampler)
  * 3-day weather     (bottom, spans 3 cols)

Weather shows only when a location is configured *and* the internet is reachable;
otherwise a tappable hint routes into System Settings (Location sheet when no
location, Internet section when offline). Clock is 24h and temperatures are °C
(matching the design defaults / the AT locale). Day/night theme auto by hour;
accent #3E84BE; weather glyphs are real tabler.io SVGs (see :mod:`display.icons`).
"""

from __future__ import annotations

import datetime as _dt
import math
import time

import pygame

from . import icons, theme, widgets
from network import weather as weather_mod

SIZE = 800
CX = SIZE // 2

# Widget grid: 560x380 centered on the 800x800 screen, 3 cols x 2 rows, gap 16.
# Origin (120, 210); col width 176, row height 182. All corners stay inside the
# round bezel (max corner radius ~338 < the 360px face radius).
TILE_RADIUS = 28
TILE_TIMEDATE = pygame.Rect(120, 210, 368, 182)   # cols 1-2
TILE_TEMP = pygame.Rect(504, 210, 176, 182)        # col 3
TILE_WEATHER = pygame.Rect(120, 408, 560, 182)     # cols 1-3

# CPU-temp status thresholds + colors (from the design).
TEMP_MIN, TEMP_MAX = 30.0, 85.0
WARM = (224, 138, 75)   # #E08A4B
HOT = (216, 90, 74)     # #D85A4A

# Condition family -> icon spec for icons.composite(): a single Tabler name, or a
# back-to-front list of layers {name, scale, dx, dy, occlude?} (dx/dy are fractions
# of the icon size). The sun always sits behind, peeking from the upper-right; the
# cloud(s) overlap in front with a knockout occluder so each composite reads as one
# glyph. "few" = sun-dominant (mostly sunny); "partly" = cloud-dominant; "cloudy" =
# two overlapping clouds; "showers" = sun + rain cloud.
_COND_ICON = {
    "sun":     "sun",
    "few":     [{"name": "sun", "scale": 0.92, "dx": 0.10, "dy": -0.08},
                {"name": "cloud", "scale": 0.55, "dx": -0.20, "dy": 0.20, "occlude": True}],
    "partly":  [{"name": "sun", "scale": 0.55, "dx": 0.24, "dy": -0.22},
                {"name": "cloud", "scale": 1.00, "dx": -0.05, "dy": 0.05, "occlude": True}],
    "cloudy":  [{"name": "cloud", "scale": 0.78, "dx": -0.16, "dy": -0.14},
                {"name": "cloud", "scale": 0.98, "dx": 0.14, "dy": 0.12, "occlude": True}],
    "rain":    "cloud-rain",
    "showers": [{"name": "sun", "scale": 0.52, "dx": 0.24, "dy": -0.24},
                {"name": "cloud-rain", "scale": 1.00, "dx": -0.05, "dy": 0.05, "occlude": True}],
    "snow":    "cloud-snow",
    "storm":   "cloud-bolt",
}


def _arc_points(center, radius, start_deg, end_deg, segments):
    cx, cy = center
    pts = []
    for i in range(segments + 1):
        a = math.radians(start_deg + (end_deg - start_deg) * i / segments)
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def _draw_arc(surf, center, radius, start_deg, end_deg, width, color):
    """Stroke a circular arc with rounded end-caps (a gauge, not an icon glyph)."""
    seg = max(2, int(abs(end_deg - start_deg) / 6))
    pts = _arc_points(center, radius, start_deg, end_deg, seg)
    ipts = [(int(round(x)), int(round(y))) for x, y in pts]
    if len(ipts) >= 2:
        pygame.draw.lines(surf, color, False, ipts, width)
    cap = max(1, width // 2)
    pygame.draw.circle(surf, color, ipts[0], cap)
    pygame.draw.circle(surf, color, ipts[-1], cap)


class InfoScreen:
    """Stateful info-screen UI. Lives in (and is driven by) the avatar thread."""

    def __init__(self, avatar, weather=None):
        self._avatar = avatar
        self.theme_name = "day"
        self._bg = None
        self._rects: dict = {}
        self._hint_target = None      # (section, sub) for the active weather hint, or None
        self._down_xy = None          # tap-down position (taps fire on release)
        self._last_kick = None        # monotonic ts of the last weather fetch nudge
        # Prefer a controller shared by main (one cache for screen + AI prompt);
        # fall back to a private one (e.g. in tests / no main binding).
        self._weather = (weather
                         or getattr(avatar, "_weather_controller", None)
                         or weather_mod.WeatherController())

    # ---------------------------------------------------------------- state

    @property
    def _settings(self) -> dict:
        return self._avatar._settings or {}

    def _get(self, path, default=None):
        d = self._settings
        for k in path:
            if not isinstance(d, dict):
                return default
            d = d.get(k)
            if d is None:
                return default
        return d

    def _location(self):
        """(lat, lon) if a usable location is configured, else None."""
        lat = self._get(("weather", "lat"))
        lon = self._get(("weather", "lon"))
        name = self._get(("weather", "location_name"))
        if lat is None or lon is None or not name:
            return None
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None

    def _cpu_avg(self):
        m = getattr(self._avatar, "_cpu_monitor", None)
        try:
            return m.average() if m is not None else None
        except Exception:
            return None

    def open(self) -> None:
        """(Re)initialise when the info screen becomes visible (left swipe)."""
        self.theme_name = theme.auto_theme()
        self._bg = None
        self._last_kick = None
        loc = self._location()
        if loc is not None:
            self._weather.start_fetch(*loc)
            self._last_kick = time.monotonic()

    # ----------------------------------------------- render signature (avatar)

    def cpu_round(self):
        a = self._cpu_avg()
        return None if a is None else round(a)

    def _weather_sig(self):
        s = self._weather.snapshot()
        return (s["state"], s["revision"], len(s["results"]))

    def sig(self):
        """Adaptive-fps repaint key: changes whenever visible content changes."""
        return ("info", theme.auto_theme(), _dt.datetime.now().strftime("%H:%M"),
                self.cpu_round(), self._weather_sig())

    # --------------------------------------------------------- event input

    def _hit(self, name, xy) -> bool:
        r = self._rects.get(name)
        return bool(r and r.collidepoint(xy))

    def handle_event(self, event) -> None:
        """Tap-through for the weather hint; fires on release if it stayed a tap.

        The horizontal swipe (right -> back to avatar) is handled by the avatar,
        so a swipe that begins on the hint must not also open settings — hence the
        movement check.
        """
        t = event.type
        if t in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
            self._down_xy = widgets.event_xy(event, SIZE)
            return
        if t in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
            up = widgets.event_xy(event, SIZE)
            down = self._down_xy
            self._down_xy = None
            if up is None or down is None:
                return
            if abs(up[0] - down[0]) > 28 or abs(up[1] - down[1]) > 28:
                return  # was a swipe/drag, not a tap
            if self._hint_target is not None and self._hit("hint_weather", up):
                self._avatar.open_system_settings_at(*self._hint_target)

    # ---------------------------------------------------------------- draw

    def draw(self, screen) -> None:
        tn = theme.auto_theme()
        if tn != self.theme_name:
            self.theme_name = tn
            self._bg = None
        th = theme.THEMES[self.theme_name]
        if self._bg is None:
            self._bg = theme.make_gradient_bg(SIZE, SIZE, th["bg_top"], th["bg_bottom"])
        screen.blit(self._bg, (0, 0))
        self._rects = {}
        self._hint_target = None

        for r in (TILE_TIMEDATE, TILE_TEMP, TILE_WEATHER):
            pygame.draw.rect(screen, th["surface"], r, border_radius=TILE_RADIUS)
        self._draw_timedate(screen, th, TILE_TIMEDATE)
        self._draw_temp(screen, th, TILE_TEMP)
        self._draw_weather(screen, th, TILE_WEATHER)

    def _draw_timedate(self, screen, th, tile) -> None:
        now = _dt.datetime.now()
        date_s = f"{now:%a} · {now:%b} {now.day}".upper()
        widgets.text(screen, date_s, 17, 700, th["muted"], center=(tile.centerx, tile.y + 58))
        widgets.text(screen, now.strftime("%H:%M"), 84, 900, th["ink"], center=(tile.centerx, tile.y + 120))

    def _draw_temp(self, screen, th, tile) -> None:
        # "DODI [CPU]" label: word + a small bordered pill.
        lbl, pill_txt = "DODI", "CPU"
        lw = widgets.text_w(lbl, 15, 800)
        pw = widgets.text_w(pill_txt, 11, 700)
        gap, pad = 8, 6
        pill_w = pw + pad * 2
        x = tile.centerx - (lw + gap + pill_w) / 2
        ly = tile.y + 28
        widgets.text(screen, lbl, 15, 800, th["muted"], midleft=(x, ly))
        pill = pygame.Rect(int(x + lw + gap), int(ly - 9), int(pill_w), 18)
        pygame.draw.rect(screen, th["ring"], pill, 2, border_radius=6)
        widgets.text(screen, pill_txt, 11, 700, th["muted"], center=pill.center)

        cx, cy, R, W = tile.centerx, tile.y + 112, 54, 12
        _draw_arc(screen, (cx, cy), R, 135, 405, W, th["ring"])
        avg = self._cpu_avg()
        if avg is None:
            widgets.text(screen, "—", 36, 900, th["muted"], center=(cx, cy - 2))
            return
        frac = max(0.0, min(1.0, (avg - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)))
        col = theme.ACCENT if avg < 60 else WARM if avg < 75 else HOT
        status = "Normal" if avg < 60 else "Warm" if avg < 75 else "Hot"
        _draw_arc(screen, (cx, cy), R, 135, 135 + 270 * frac, W, col)
        widgets.text(screen, f"{round(avg)}°", 34, 900, th["ink"], center=(cx, cy - 4))
        widgets.text(screen, status, 13, 800, col, center=(cx, cy + 26))

    def _draw_weather(self, screen, th, tile) -> None:
        loc = self._location()
        if loc is None:
            self._draw_hint(screen, th, tile, "Configure location for weather service.",
                            "map-pin", ("internet", "location"))
            return
        # Nudge a refresh on open and ~once a minute (controller throttles/caches,
        # so this never fetches per-frame).
        now = time.monotonic()
        if self._last_kick is None or now - self._last_kick > 60:
            self._weather.start_fetch(*loc)
            self._last_kick = now
        snap = self._weather.snapshot()
        if snap["results"]:
            self._draw_forecast(screen, th, tile, snap["results"])
        elif snap["state"] == "offline":
            self._draw_hint(screen, th, tile, "Connect to internet for weather service.",
                            "wifi", ("internet", None))
        else:
            widgets.text(screen, "Loading…", 18, 700, th["muted"], center=tile.center)

    def _draw_forecast(self, screen, th, tile, results) -> None:
        n = min(3, len(results))
        cw = tile.width / 3.0
        for i in range(n):
            cx = tile.x + cw * (i + 0.5)
            if i > 0:
                sx = int(tile.x + cw * i)
                pygame.draw.line(screen, th["ring"], (sx, tile.y + 26), (sx, tile.bottom - 26), 2)
            d = results[i]
            day = "Today" if i == 0 else self._dow(d.date)
            widgets.text(screen, day, 17, 800, theme.ACCENT if i == 0 else th["ink"],
                         center=(cx, tile.y + 32))
            icons.composite(screen, _COND_ICON.get(d.type, "cloud"),
                            int(cx), int(tile.y + 90), 56, theme.ACCENT, th["surface"])
            hi, lo = f"{round(d.hi)}°", f"{round(d.lo)}°"
            hw, lw = widgets.text_w(hi, 23, 900), widgets.text_w(lo, 17, 700)
            gap = 9
            x = cx - (hw + gap + lw) / 2
            widgets.text(screen, hi, 23, 900, th["ink"], midleft=(x, tile.y + 150))
            widgets.text(screen, lo, 17, 700, th["muted"], midleft=(x + hw + gap, tile.y + 150))

    def _draw_hint(self, screen, th, tile, msg, icon, target) -> None:
        self._rects["hint_weather"] = tile
        self._hint_target = target
        icons.blit(screen, icon, tile.centerx, tile.y + 56, 34, th["muted"])
        widgets.text(screen, msg, 18, 700, th["muted"], center=(tile.centerx, tile.y + 104))
        widgets.text(screen, "Tap to open settings", 13, 700, th["muted"],
                     center=(tile.centerx, tile.y + 138))

    @staticmethod
    def _dow(iso: str) -> str:
        try:
            return _dt.date.fromisoformat(iso).strftime("%a")
        except (ValueError, TypeError):
            return ""
