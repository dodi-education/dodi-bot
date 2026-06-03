"""Backlight control via /sys/class/backlight (Raspberry Pi DSI panel).

Best-effort: every write is wrapped in try/except + log so a missing sysfs path
or permission denial does not take down the night-mode flow.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("DODI.night_mode")

_BACKLIGHT_ROOT = Path("/sys/class/backlight")


def _find_backlight() -> Optional[Path]:
    if not _BACKLIGHT_ROOT.exists():
        return None
    candidates = sorted(_BACKLIGHT_ROOT.iterdir())
    return candidates[0] if candidates else None


def set_brightness(pct: int) -> None:
    """Set backlight brightness as a percentage (0-100)."""
    pct = max(0, min(100, int(pct)))
    bl = _find_backlight()
    if bl is None:
        logger.debug("No backlight device found; skipping brightness")
        return
    try:
        max_b = int((bl / "max_brightness").read_text().strip())
        value = int(round(pct / 100.0 * max_b))
        (bl / "brightness").write_text(str(value))
        logger.info(f"Backlight brightness set to {pct}% ({value}/{max_b})")
    except Exception as e:
        logger.warning(f"Could not set brightness: {e}")


def set_power(on: bool) -> None:
    """Power the backlight on or off via bl_power (0=on, 1=off)."""
    bl = _find_backlight()
    if bl is None:
        logger.debug("No backlight device found; skipping power")
        return
    try:
        (bl / "bl_power").write_text("0" if on else "1")
        logger.info(f"Backlight power: {'on' if on else 'off'}")
    except Exception as e:
        logger.warning(f"Could not set backlight power: {e}")
