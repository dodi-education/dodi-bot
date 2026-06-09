"""Device power control: reboot / shutdown.

Triggered from the Power section of the system settings overlay. Both go through
``sudo -n systemctl`` — the same passwordless-sudo path Wi-Fi already relies on
(see :mod:`network.wifi`), so no extra sudoers rule is needed on a stock Pi. The
systemd unit (``dodi.service``) is stopped cleanly by systemd as it transitions
to the reboot/poweroff target.

Best-effort and logged: a failure here must never crash the UI thread.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("DODI.system")


def _systemctl(action: str, timeout: float = 5.0) -> bool:
    try:
        cp = subprocess.run(
            ["sudo", "-n", "systemctl", action],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        logger.warning("systemctl %s failed: %s", action, e)
        return False
    if cp.returncode != 0:
        logger.warning("systemctl %s failed: %s", action, cp.stderr.strip())
        return False
    return True


def reboot() -> bool:
    """Reboot the device."""
    logger.info("Reboot requested")
    return _systemctl("reboot")


def shutdown() -> bool:
    """Power the device off."""
    logger.info("Shutdown requested")
    return _systemctl("poweroff")
