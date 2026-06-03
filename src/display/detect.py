"""Screen detection for Waveshare 3.4-inch DSI Round Touch Display."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("DODI.display")

EXPECTED_WIDTH = 800
EXPECTED_HEIGHT = 800


def detect_screen() -> bool:
    """
    Detect if the round DSI display is attached and available.

    Checks for framebuffer device and verifies it matches the expected
    800x800 resolution of the Waveshare round display.

    Returns:
        True if the round display is detected, False otherwise.
    """
    fb_path = Path("/dev/fb0")
    if not fb_path.exists():
        logger.info("No framebuffer device found at /dev/fb0")
        return False

    # Check resolution via fbset or sysfs
    try:
        fb_size = Path("/sys/class/graphics/fb0/virtual_size").read_text().strip()
        width, height = fb_size.split(",")
        if int(width) == EXPECTED_WIDTH and int(height) >= EXPECTED_HEIGHT:
            logger.info(f"Round display detected: {width}x{height}")
            return True
    except (FileNotFoundError, ValueError):
        pass

    # Fallback: check via DRM connector status
    try:
        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            for connector in drm_path.iterdir():
                status_file = connector / "status"
                if status_file.exists():
                    status = status_file.read_text().strip()
                    if status == "connected":
                        modes_file = connector / "modes"
                        if modes_file.exists():
                            modes = modes_file.read_text()
                            if "800x800" in modes:
                                logger.info(f"Round display detected via DRM: {connector.name}")
                                return True
    except (PermissionError, OSError) as e:
        logger.debug(f"DRM check failed: {e}")

    # Final fallback: just check if any display is connected
    try:
        result = subprocess.run(
            ["cat", "/sys/class/graphics/fb0/name"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info(f"Framebuffer found: {result.stdout.strip()}")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    logger.info("No compatible display detected")
    return False
