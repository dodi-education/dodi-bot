"""Tabler SVG icon loader tests (headless pygame).

Confirms the vendored Tabler SVGs rasterize via SDL_image, recolor, size, and cache.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

pygame.init()

from display import icons  # noqa: E402

# Every icon the system-settings UI references must exist + load.
USED = [
    "volume", "bell", "trending-up", "wifi", "map-pin", "power", "moon", "sun",
    "clock", "calendar", "chevron-right", "arrow-left", "x", "lock", "search",
    "plus", "minus", "check", "trash", "backspace", "device-desktop",
    # info screen weather glyphs
    "cloud", "cloud-rain", "cloud-snow",
]


def test_all_used_icons_present_on_disk():
    missing = [n for n in USED if not (icons.ICON_DIR / f"{n}.svg").exists()]
    assert missing == [], f"missing vendored Tabler SVGs: {missing}"


def test_get_returns_sized_alpha_surface():
    for name in USED:
        surf = icons.get(name, 32, (62, 132, 190))
        assert surf.get_size() == (32, 32), name
        assert surf.get_flags() & pygame.SRCALPHA, name
        # Something actually rasterized (stroke pixels present).
        assert pygame.mask.from_surface(surf).count() > 0, f"{name} rendered empty"


def test_size_attr_controls_raster_resolution():
    small = icons.get("wifi", 24, (0, 0, 0))
    big = icons.get("wifi", 96, (0, 0, 0))
    assert small.get_size() == (24, 24)
    assert big.get_size() == (96, 96)


def test_cache_returns_same_object():
    a = icons.get("check", 20, (255, 255, 255))
    b = icons.get("check", 20, (255, 255, 255))
    assert a is b
    # Different color/size is a distinct cache entry.
    assert icons.get("check", 20, (0, 0, 0)) is not a
    assert icons.get("check", 40, (255, 255, 255)) is not a


def test_missing_icon_is_transparent_not_crash():
    surf = icons.get("does-not-exist", 24, (10, 20, 30))
    assert surf.get_size() == (24, 24)
    assert pygame.mask.from_surface(surf).count() == 0


def test_blit_centers_and_returns_rect():
    screen = pygame.Surface((100, 100), pygame.SRCALPHA)
    rect = icons.blit(screen, "moon", 50, 50, 30, (33, 56, 78))
    assert rect.center == (50, 50)
    assert rect.size == (30, 30)
