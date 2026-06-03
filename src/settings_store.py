"""Read/write the JSON settings file (config/settings.json).

The in-memory settings dict is the single source of truth shared across threads
(the control loop, the alarm manager, the settings screen). Writes are atomic
(temp file + os.replace) and serialized with a lock so concurrent savers — e.g.
the settings screen and the alarm manager auto-deactivating a one-time alarm —
can't clobber each other.
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("DODI")

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config" / "settings.json"

_save_lock = threading.Lock()


def load_settings() -> dict:
    """Load settings from config/settings.json (empty dict if missing)."""
    if not CONFIG_PATH.exists():
        logger.warning(f"Settings file not found: {CONFIG_PATH}")
        return {}
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_settings(settings: dict) -> bool:
    """Atomically persist the settings dict. Returns True on success.

    Writes to a temp file then os.replace()s it into place, so a crash mid-write
    can never leave a truncated settings.json. Callers mutate values in-place
    (not the key structure), so serialization is safe alongside readers.
    """
    with _save_lock:
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
            return True
        except Exception as e:
            logger.error(f"Could not save settings: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False
