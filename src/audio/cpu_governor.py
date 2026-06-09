"""CPU-governor boost for glitch-free audio playback.

On a Raspberry Pi the default ``ondemand`` CPU governor downclocks during the
near-idle steady state of audio playback (the callback only copies a few KB
every ~125ms). The DVFS transition starves PortAudio's callback thread and
produces a one-off dropout a few seconds into playback — the audible stutter at
the start of every alarm. Empirically confirmed: the underrun coincides exactly
with a 1.8GHz->700MHz drop, and pinning ``performance`` eliminates it.

So we pin ``performance`` while audio is active and restore the previous
governor when the last playback ends. Best-effort and refcounted: overlapping
playbacks share one boost, and any failure (no cpufreq, no permission) is logged
once and ignored — audio must always play, glitch or not. Writing the governor
needs root, so we try a direct sysfs write first (works when running as root,
e.g. under the systemd service) and fall back to the ``sudo -n`` path the rest
of the app already relies on (see :mod:`system_control` and :mod:`network.wifi`).
"""

from __future__ import annotations

import glob
import logging
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_GOVERNOR_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
_CPU0_GOVERNOR = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
_BOOST = "performance"

_lock = threading.Lock()
_refcount = 0
_saved: Optional[str] = None   # governor to restore when refcount returns to 0
_boosted = False               # did we actually change it (perms ok)?
_warned = False                # emit the can't-set warning only once


def _read_governor() -> Optional[str]:
    try:
        with open(_CPU0_GOVERNOR) as f:
            return f.read().strip()
    except Exception:
        return None


def _write_governor(value: str) -> bool:
    """Set the governor on every CPU. Direct write, else passwordless sudo."""
    paths = sorted(glob.glob(_GOVERNOR_GLOB))
    if not paths:
        return False
    try:
        for p in paths:
            with open(p, "w") as f:
                f.write(value)
        return True
    except PermissionError:
        pass
    except Exception as e:
        logger.debug("governor direct write failed: %s", e)
    cmd = 'for f in %s; do echo %s > "$f"; done' % (_GOVERNOR_GLOB, value)
    try:
        cp = subprocess.run(
            ["sudo", "-n", "sh", "-c", cmd],
            capture_output=True, text=True, timeout=5.0,
        )
        return cp.returncode == 0
    except Exception as e:
        logger.debug("governor sudo write failed: %s", e)
        return False


def acquire() -> None:
    """Pin the performance governor for the duration of audio playback."""
    global _refcount, _saved, _boosted, _warned
    with _lock:
        _refcount += 1
        if _refcount > 1:
            return  # already boosted by an overlapping playback
        current = _read_governor()
        if current is None or current == _BOOST:
            # No cpufreq (dev box) or someone already pinned performance:
            # nothing for us to change or restore.
            _saved = None
            _boosted = False
            return
        if _write_governor(_BOOST):
            _saved = current
            _boosted = True
            logger.info("CPU governor %s -> %s for playback", current, _BOOST)
        else:
            _saved = None
            _boosted = False
            if not _warned:
                logger.warning(
                    "Could not set CPU governor to %s (needs root/sudo); "
                    "audio may stutter briefly at start", _BOOST,
                )
                _warned = True


def release() -> None:
    """Restore the previous governor once the last playback ends."""
    global _refcount, _saved, _boosted
    with _lock:
        if _refcount == 0:
            return
        _refcount -= 1
        if _refcount > 0:
            return
        if _boosted and _saved:
            if _write_governor(_saved):
                logger.info("CPU governor restored -> %s", _saved)
        _saved = None
        _boosted = False
