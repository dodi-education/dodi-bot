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


def scrim(screen, rgba=(12, 24, 36, 128), size: int = SIZE) -> None:
    """Dim the whole screen behind a modal sheet."""
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    s.fill(rgba)
    screen.blit(s, (0, 0))


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
