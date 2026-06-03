#!/usr/bin/env python3
"""Multi-touch probe — confirms whether the app's SDL/pygame stack delivers
more than one simultaneous finger (needed for a two-finger swipe gesture).

Run this with the main DODI app STOPPED (only one process can own the KMSDRM
display at a time):

    source venv/bin/activate
    python scripts/touch_probe.py

Then touch the screen:
  * one finger  -> you should see a single FINGERDOWN
  * two fingers (at once) -> you should see TWO FINGERDOWNs with different ids,
    and the banner "*** MULTI-TOUCH OK: 2 fingers down ***"

It also prints FINGERMOTION dy so you can see swipe direction is reported.
Press ESC (or Ctrl+C) to quit; it also auto-quits after ~40s.
"""

import os
import sys
import time
from pathlib import Path


def _ensure_venv():
    """Re-exec with the project venv's python if not already running under it.

    (Activating the venv doesn't reliably repoint `python` on this Pi, so we
    re-exec the same way src/main.py does.)
    """
    venv_python = Path(__file__).resolve().parent.parent / "venv" / "bin" / "python"
    venv_root = venv_python.parent.parent
    in_project_venv = hasattr(sys, "prefix") and Path(sys.prefix).resolve() == venv_root.resolve()
    if not in_project_venv and venv_python.exists():
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)


_ensure_venv()

# Match avatar.py: force KMSDRM on the headless Pi with the DSI panel.
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ["SDL_VIDEODRIVER"] = "KMSDRM"

import pygame

SCREEN = 800
DEADLINE = 40.0  # seconds


def main() -> None:
    pygame.init()
    try:
        screen = pygame.display.set_mode((SCREEN, SCREEN), pygame.FULLSCREEN | pygame.NOFRAME)
    except pygame.error:
        screen = pygame.display.set_mode((SCREEN, SCREEN), pygame.NOFRAME)
    pygame.mouse.set_visible(False)

    n_touch = pygame.get_num_touch_devices() if hasattr(pygame, "get_num_touch_devices") else "?"
    print(f"SDL video driver : {pygame.display.get_driver()}")
    print(f"touch devices    : {n_touch}")
    print("Touch the screen. Try one finger, then two fingers at once.")
    print("-" * 60)

    active = {}          # finger_id -> (x, y)
    max_seen = 0
    start = time.monotonic()
    clock = pygame.time.Clock()
    running = True

    while running and time.monotonic() - start < DEADLINE:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.FINGERDOWN:
                active[event.finger_id] = (event.x, event.y)
                max_seen = max(max_seen, len(active))
                print(f"FINGERDOWN  id={event.finger_id}  x={event.x:.3f} y={event.y:.3f}  "
                      f"fingers_down={len(active)}")
                if len(active) >= 2:
                    print(f"*** MULTI-TOUCH OK: {len(active)} fingers down ***")
            elif event.type == pygame.FINGERMOTION:
                # dy > 0 means moving down the screen (normalised coords).
                print(f"FINGERMOTION id={event.finger_id}  dx={event.dx:+.3f} dy={event.dy:+.3f}")
            elif event.type == pygame.FINGERUP:
                active.pop(event.finger_id, None)
                print(f"FINGERUP    id={event.finger_id}  fingers_down={len(active)}")
            elif event.type == pygame.MOUSEBUTTONDOWN:
                # If you ONLY ever see this and never FINGERDOWN, SDL is giving
                # mouse-emulation only (single-touch).
                print(f"MOUSEBUTTONDOWN pos={event.pos}  (mouse-emulation event)")

        screen.fill((20, 20, 30))
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    print("-" * 60)
    print(f"RESULT: max simultaneous fingers seen = {max_seen}")
    if max_seen >= 2:
        print("=> Multi-touch WORKS through pygame. Two-finger gestures are feasible.")
    else:
        print("=> Only single-touch came through pygame. Two-finger gestures NOT")
        print("   available via this stack (even if the panel hardware supports MT).")


if __name__ == "__main__":
    main()
