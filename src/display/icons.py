"""Tabler icon loader for the pygame UI.

The whole Dodi UI uses real tabler.io icons (never hand-drawn approximations).
Tabler ships them as stroke SVGs; SDL_image (>=2.8 in this venv) rasterizes SVG
natively, so we load the vendored ``assets/icons/tabler/<name>.svg`` files,
substitute the stroke color + raster size, and cache the resulting Surface.

Usage::

    from display import icons
    icons.blit(screen, "wifi", cx, cy, 26, theme.ACCENT)
    surf = icons.get("check", 20, (255, 255, 255))
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pygame

logger = logging.getLogger("DODI.display")

ICON_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "icons" / "tabler"

# (name, size, color) -> Surface. Cleared only on process exit.
_CACHE: dict[tuple[str, int, tuple[int, int, int]], pygame.Surface] = {}
# Raw SVG text per name, read once.
_SVG_TEXT: dict[str, str] = {}
# A 1x1 transparent fallback so a missing/broken icon never crashes a draw.
_EMPTY: pygame.Surface | None = None


def _hex(color) -> str:
    r, g, b = color[0], color[1], color[2]
    return f"#{r:02x}{g:02x}{b:02x}"


def _empty(size: int) -> pygame.Surface:
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    return s


def _load_text(name: str) -> str | None:
    txt = _SVG_TEXT.get(name)
    if txt is None:
        path = ICON_DIR / f"{name}.svg"
        try:
            txt = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Tabler icon not found: %s", path)
            return None
        _SVG_TEXT[name] = txt
    return txt


def get(name: str, size: int, color) -> pygame.Surface:
    """Return a cached, recolored, ``size``x``size`` Surface for icon ``name``.

    ``color`` is an (r, g, b) tuple. Missing/broken icons return a transparent
    surface (logged once) rather than raising.
    """
    color = (int(color[0]), int(color[1]), int(color[2]))
    key = (name, size, color)
    surf = _CACHE.get(key)
    if surf is not None:
        return surf

    txt = _load_text(name)
    if txt is None:
        return _empty(size)

    svg = (
        txt.replace('stroke="currentColor"', f'stroke="{_hex(color)}"')
        .replace('fill="currentColor"', f'fill="{_hex(color)}"')  # filled glyphs
        .replace('width="24"', f'width="{size}"')
        .replace('height="24"', f'height="{size}"')
    )
    try:
        surf = pygame.image.load(io.BytesIO(svg.encode("utf-8")), "icon.svg")
    except Exception as e:  # pragma: no cover - depends on SDL_image SVG support
        logger.warning("Could not rasterize icon %s: %s", name, e)
        _CACHE[key] = _empty(size)
        return _CACHE[key]
    # convert_alpha() speeds up blits but needs a display surface; skip it headless.
    if pygame.display.get_surface() is not None:
        try:
            surf = surf.convert_alpha()
        except pygame.error:
            pass
    _CACHE[key] = surf
    return surf


def blit(screen: pygame.Surface, name: str, cx: int, cy: int, size: int, color) -> pygame.Rect:
    """Blit icon ``name`` centered at (cx, cy). Returns the blit rect."""
    surf = get(name, size, color)
    rect = surf.get_rect(center=(int(cx), int(cy)))
    screen.blit(surf, rect)
    return rect


# Occluder shape for composites: a solid cloud painted in the background color
# erases whatever sits behind a front cloud so the layers read as one glyph.
_OCCLUDER = "cloud-filled"
_OCCLUDER_SCALE = 1.12  # slight oversize -> a clean knockout halo around the cloud


def composite(screen: pygame.Surface, spec, cx: int, cy: int, size: int,
              color, occluder_color) -> pygame.Rect:
    """Draw a single icon OR an overlapping multi-layer composite as one glyph.

    ``spec`` is either:
      * a ``str`` — a single Tabler icon name (delegates to :func:`blit`), or
      * a ``list`` of layer dicts drawn back-to-front, each
        ``{"name", "scale", "dx", "dy", "occlude"?}`` where ``scale`` is a
        fraction of ``size`` and ``dx``/``dy`` are center offsets as fractions of
        ``size``. A layer with ``"occlude": True`` first stamps a background-colored
        filled cloud (the knockout) so the glyph(s) behind it are hidden where the
        cloud sits — making e.g. a sun-behind-cloud read as a single symbol.

    ``color`` paints the visible glyphs; ``occluder_color`` is the tile background
    color (so the knockout matches the local background in any theme). Returns the
    overall bounding rect.
    """
    if isinstance(spec, str):
        return blit(screen, spec, cx, cy, size, color)

    bounds = None
    for layer in spec:
        ls = max(1, int(round(size * layer.get("scale", 1.0))))
        lx = int(round(cx + layer.get("dx", 0.0) * size))
        ly = int(round(cy + layer.get("dy", 0.0) * size))
        if layer.get("occlude"):
            occ = get(_OCCLUDER, max(1, int(round(ls * _OCCLUDER_SCALE))), occluder_color)
            screen.blit(occ, occ.get_rect(center=(lx, ly)))
        rect = blit(screen, layer["name"], lx, ly, ls, color)
        bounds = rect if bounds is None else bounds.union(rect)
    return bounds if bounds is not None else pygame.Rect(cx, cy, 0, 0)
