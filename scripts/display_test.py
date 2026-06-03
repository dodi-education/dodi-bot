#!/usr/bin/env python3
"""
Minimal display smoke-test for the Waveshare 3.4" round DSI panel.

Drives the panel through the same SDL KMSDRM path the avatar uses and cycles
through solid full-screen colors so you can visually confirm the screen is
actually painting. Auto-exits after a few seconds so it is safe to run over
SSH / in the background.

Run:  ./venv/bin/python scripts/display_test.py
"""

import os
import sys
import time

# Match avatar.py: use KMS/DRM when there is no X/Wayland session (e.g. over SSH).
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")

import pygame

SCREEN_SIZE = 800
HOLD_SECONDS = 1.5
COLORS = [
    ("RED", (220, 40, 40)),
    ("GREEN", (40, 200, 80)),
    ("BLUE", (40, 90, 220)),
    ("WHITE", (240, 240, 240)),
]


def main() -> int:
    print(f"SDL_VIDEODRIVER requested: {os.environ.get('SDL_VIDEODRIVER', '(default)')}")
    pygame.init()

    try:
        screen = pygame.display.set_mode(
            (SCREEN_SIZE, SCREEN_SIZE), pygame.FULLSCREEN | pygame.NOFRAME
        )
    except pygame.error as e:
        print(f"FULLSCREEN set_mode failed ({e}); retrying windowed/NOFRAME...")
        try:
            screen = pygame.display.set_mode((SCREEN_SIZE, SCREEN_SIZE), pygame.NOFRAME)
        except pygame.error as e2:
            print(f"ERROR: could not initialize any display mode: {e2}")
            pygame.quit()
            return 1

    actual_driver = pygame.display.get_driver()
    w, h = screen.get_size()
    print(f"Display initialized OK. driver={actual_driver}  surface={w}x{h}")

    pygame.mouse.set_visible(False)

    try:
        font = pygame.font.SysFont(None, 90)
    except Exception as e:  # noqa: BLE001
        print(f"Font init failed (continuing without text): {e}")
        font = None

    for name, rgb in COLORS:
        screen.fill(rgb)
        if font is not None:
            text_color = (0, 0, 0) if name == "WHITE" else (255, 255, 255)
            label = font.render(f"{name}", True, text_color)
            rect = label.get_rect(center=(SCREEN_SIZE // 2, SCREEN_SIZE // 2))
            screen.blit(label, rect)
        pygame.display.flip()
        print(f"  showing {name} {rgb}")
        # Drain events so the window manager / KMSDRM stays responsive.
        deadline = time.monotonic() + HOLD_SECONDS
        while time.monotonic() < deadline:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    return 0
            time.sleep(0.02)

    print("Done. Screen should have cycled RED -> GREEN -> BLUE -> WHITE.")
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
