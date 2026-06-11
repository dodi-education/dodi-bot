"""Shared theme + asset helpers for the alarm UI.

Colors come straight from the Claude Design "Dodi-Bot Alarm" CSS (day/night
themes, accent #3E84BE). Fonts are the bundled Nunito weights. The alarm icon
is drawn procedurally in the Tabler line-icon style (pygame can't render SVG).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pygame

_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
FONTS_DIR = _ASSETS / "fonts"

ACCENT = (62, 132, 190)  # #3E84BE

# Day / night palettes (from the design's CSS variables).
THEMES = {
    "day": {
        "ink": (33, 56, 78),        # #21384e
        "muted": (138, 166, 189),   # #8aa6bd
        "surface": (255, 255, 255),
        "surface2": (233, 243, 250),  # #e9f3fa
        "ring": (211, 229, 241),    # #d3e5f1
        "bg_top": (255, 255, 255),
        "bg_bottom": (216, 235, 247),  # #d8ebf7
        "knob": (255, 255, 255),
    },
    "night": {
        "ink": (233, 241, 249),     # #e9f1f9
        "muted": (119, 145, 168),   # #7791a8
        "surface": (39, 57, 77),    # #27394d
        "surface2": (29, 44, 61),   # #1d2c3d
        "ring": (52, 73, 95),       # #34495f
        "bg_top": (33, 52, 74),     # #21344a
        "bg_bottom": (15, 25, 36),  # #0f1924
        "knob": (255, 255, 255),
    },
}

_WEIGHTS = {400: "Regular", 700: "Bold", 800: "ExtraBold", 900: "Black"}
_FONT_CACHE: dict = {}


def font(size: int, weight: int = 800) -> pygame.font.Font:
    """Cached Nunito font at the given pixel size + weight (400/700/800/900)."""
    key = (size, weight)
    f = _FONT_CACHE.get(key)
    if f is None:
        name = _WEIGHTS.get(weight, "ExtraBold")
        try:
            f = pygame.font.Font(str(FONTS_DIR / f"Nunito-{name}.ttf"), size)
        except Exception:
            f = pygame.font.SysFont(None, size)
        _FONT_CACHE[key] = f
    return f


def _hhmm_to_minutes(hhmm, default_min: int) -> int:
    """Parse "HH:MM" into minutes-since-midnight; fall back to default on bad input.

    "24:00" maps to end-of-day (1440). Other fields are clamped into range so a
    malformed value can never crash the render loop.
    """
    try:
        hh, mm = str(hhmm).split(":")
        h, m = int(hh), int(mm)
    except Exception:
        return default_min
    if h == 24 and m == 0:
        return 24 * 60
    return (h % 24) * 60 + (m % 60)


def resolve_theme(theme_cfg: dict | None = None, now: _dt.datetime | None = None) -> str:
    """Resolve the active palette name ("day"/"night") from the theme settings.

    `theme_cfg` is the "theme" settings block::

        {"mode": "auto" | "light" | "dark",
         "dark_mode_start": "HH:MM", "dark_mode_end": "HH:MM"}

    - "light": always the day palette.
    - "dark":  always the night palette.
    - "auto" (default): night between ``dark_mode_start`` and ``dark_mode_end``.
      The window wraps past midnight when start > end (e.g. "22:00"-"06:00");
      an empty window (start == end) is never dark.
    """
    cfg = theme_cfg or {}
    mode = str(cfg.get("mode", "auto")).strip().lower()
    if mode == "light":
        return "day"
    if mode == "dark":
        return "night"

    # "auto" (and any unknown mode): time-bounded dark window.
    start = _hhmm_to_minutes(cfg.get("dark_mode_start"), 18 * 60)
    end = _hhmm_to_minutes(cfg.get("dark_mode_end"), 8 * 60)
    if start == end:
        return "day"  # empty window -> never dark
    cur = now or _dt.datetime.now()
    cur_min = cur.hour * 60 + cur.minute
    if start < end:
        is_night = start <= cur_min < end
    else:  # window wraps past midnight
        is_night = cur_min >= start or cur_min < end
    return "night" if is_night else "day"


def make_gradient_bg(w: int, h: int, c_top, c_bottom) -> pygame.Surface:
    """Build a vertical gradient surface (approximates the design's radial bg)."""
    bg = pygame.Surface((w, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col = (
            int(c_top[0] + (c_bottom[0] - c_top[0]) * t),
            int(c_top[1] + (c_bottom[1] - c_top[1]) * t),
            int(c_top[2] + (c_bottom[2] - c_top[2]) * t),
        )
        pygame.draw.line(bg, col, (0, y), (w, y))
    return bg


def draw_alarm_icon(surface, cx: int, cy: int, size: int, color, width: int | None = None) -> None:
    """Draw a Tabler-style alarm-clock icon centered at (cx, cy), fitting `size` px.

    Mirrors Tabler's `alarm` glyph (24x24 viewBox): a clock face, an up+right
    hand, and two angled "bell legs" at the top.
    """
    s = size / 24.0
    if width is None:
        width = max(2, round(size / 12))

    def pt(x, y):
        return (int(round(cx + (x - 12) * s)), int(round(cy + (y - 12) * s)))

    pygame.draw.circle(surface, color, pt(12, 13), int(round(7 * s)), width)   # face
    pygame.draw.line(surface, color, pt(12, 13), pt(12, 9.5), width)           # hand up
    pygame.draw.line(surface, color, pt(12, 13), pt(14.5, 13), width)          # hand right
    pygame.draw.line(surface, color, pt(7, 4), pt(4.25, 6), width)             # left leg
    pygame.draw.line(surface, color, pt(17, 4), pt(19.75, 6), width)           # right leg
