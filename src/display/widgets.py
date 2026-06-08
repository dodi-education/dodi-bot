"""Shared pygame draw/input primitives for the touch UIs (alarm + system settings).

These were factored out of ``settings_screen.py`` so both overlays share one
implementation of text rendering, touch-coordinate normalization, the scrim, the
toggle switch, etc. Geometry is for the round 800x800 screen, centered on CX.
"""

from __future__ import annotations

import pygame

from . import theme

SIZE = 800
CX = SIZE // 2


def text(screen, s, size, weight, color, center=None, topleft=None, midleft=None):
    """Render Nunito text with flexible anchoring; returns the blit rect."""
    surf = theme.font(size, weight).render(s, True, color)
    if center is not None:
        rect = surf.get_rect(center=center)
    elif midleft is not None:
        rect = surf.get_rect(midleft=midleft)
    else:
        rect = surf.get_rect(topleft=topleft)
    screen.blit(surf, rect)
    return rect


def text_w(s, size, weight) -> int:
    """Pixel width of ``s`` at the given font size/weight."""
    return theme.font(size, weight).size(s)[0]


def event_xy(event, size: int = SIZE):
    """Pixel (x, y) for a finger/mouse event, or None for non-positional events.

    Finger events carry normalized 0..1 coords; mouse events carry ``pos``.
    """
    if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
        return event.x * size, event.y * size
    if hasattr(event, "pos"):
        return event.pos
    return None


def scrim(screen, rgba=(12, 24, 36, 128), size: int = SIZE, blur: bool = True) -> None:
    """Blur + dim the whole screen behind a modal sheet.

    The blur is a cheap gaussian-ish softening (downscale then upscale with
    bilinear filtering) approximating the design's ``backdrop-filter: blur(3px)``
    — subtle, the backdrop stays recognizable. The ``rgba`` dim is kept on top.
    """
    if blur:
        small = pygame.transform.smoothscale(screen, (size // 4, size // 4))
        screen.blit(pygame.transform.smoothscale(small, (size, size)), (0, 0))
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    s.fill(rgba)  # alpha 128 == design's rgba(12,24,36,.5)
    screen.blit(s, (0, 0))


CLOSE_SIZE = 85    # bottom-centre dismiss button (was 64; +1/3)
CLOSE_MARGIN = 34  # gap from the bottom edge of the round screen


def close_button(screen, th) -> pygame.Rect:
    """Bottom-centre round dismiss button with a Tabler ✕. Returns its rect.

    Shared by the alarm screen, the dial overlay's cancel, and system settings
    so the close affordance is identical everywhere.
    """
    from . import icons  # local import avoids any import cycle with icons
    r = pygame.Rect(CX - CLOSE_SIZE // 2, SIZE - CLOSE_MARGIN - CLOSE_SIZE,
                    CLOSE_SIZE, CLOSE_SIZE)
    pygame.draw.circle(screen, th["surface2"], r.center, CLOSE_SIZE // 2)
    icons.blit(screen, "x", r.centerx, r.centery, 35, th["muted"])
    return r


def alpha_circle(screen, center, r, rgba) -> None:
    """Filled translucent circle (RGBA) blitted onto ``screen``."""
    s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(s, rgba, (r, r), r)
    screen.blit(s, (center[0] - r, center[1] - r))


def draw_switch(screen, th, x, y, on) -> pygame.Rect:
    """iOS-style toggle (74x42). Accent track when on. Returns the track rect."""
    track = pygame.Rect(x, y, 74, 42)
    pygame.draw.rect(screen, theme.ACCENT if on else th["ring"], track, border_radius=21)
    knob_x = x + 37 if on else x + 5
    pygame.draw.circle(screen, (255, 255, 255), (knob_x + 16, y + 21), 16)
    return track
