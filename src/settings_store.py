"""Read/write the JSON settings file (config/settings.json).

config/settings.json is per-device and untracked (gitignored); the committed
config/default_settings.json is the template. On first run (no settings.json),
we seed it from default_settings.json so a fresh checkout boots with sane values.

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
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config" / "default_settings.json"

_save_lock = threading.Lock()


def load_settings() -> dict:
    """Load settings from config/settings.json.

    If settings.json is missing (e.g. a fresh checkout), seed it from
    config/default_settings.json and persist the copy. Returns an empty dict
    only if neither file exists or the JSON is unreadable.
    """
    if not CONFIG_PATH.exists():
        if not DEFAULT_CONFIG_PATH.exists():
            logger.warning(
                f"No settings file and no defaults to seed from: {CONFIG_PATH}"
            )
            return {}
        logger.info(f"No settings.json; seeding from {DEFAULT_CONFIG_PATH.name}")
        with open(DEFAULT_CONFIG_PATH, "r") as f:
            defaults = json.load(f)
        save_settings(defaults)
        return defaults
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
