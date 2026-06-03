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


def auto_theme(now: _dt.datetime | None = None) -> str:
    """Night theme between 18:00 and 08:00 (alarms are set in the evening)."""
    h = (now or _dt.datetime.now()).hour
    return "night" if (h >= 18 or h < 8) else "day"


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
