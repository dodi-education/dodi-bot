"""Optional file logging for DODI.

By default DODI logs to the console, which systemd captures into the journal
(`journalctl -u dodi`). That journal is volatile on this Pi, so logs are lost on
reboot. Setting `debug.log_file` in config/settings.json adds a second sink: a
plain log file that survives reboots and can be copied off the device.

The file is *size-capped*, not rotated: once it grows past `debug.max_log_size`
(default 100M) we stop appending and leave the file untouched. This keeps the
oldest debug context intact (the run that first misbehaved) and guarantees the
log can never fill the SD card. To resume logging, delete/shrink the file and
restart the service.

If `log_file` is null (the default), nothing here changes — console/journal
logging behaves exactly as before.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("DODI")

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Shared with main.py so console and file lines look identical.
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

DEFAULT_MAX_LOG_SIZE = "100M"

_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(value) -> int:
    """Parse a human-friendly size into bytes.

    Accepts ints (bytes), or strings like "100M", "100MB", "512k", "1G", or a
    bare number ("1048576"). Returns 0 for None/empty, which callers treat as
    "no limit". Raises ValueError on anything unparseable.
    """
    if value is None:
        return 0
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise ValueError(f"invalid size: {value!r}")
    if isinstance(value, (int, float)):
        return int(value)

    s = str(value).strip().upper()
    if not s:
        return 0
    if s.endswith("B"):  # allow "100MB" == "100M"
        s = s[:-1]
    mult = 1
    if s and s[-1] in _UNITS:
        mult = _UNITS[s[-1]]
        s = s[:-1]
    return int(float(s) * mult)


class CappedFileHandler(logging.FileHandler):
    """A FileHandler that stops writing once the file passes ``max_bytes``.

    Unlike :class:`logging.handlers.RotatingFileHandler`, nothing is rotated or
    truncated. When the cap is reached we write one final notice and then drop
    every further record, so the file is frozen at roughly ``max_bytes`` and the
    earliest logs are preserved. ``max_bytes <= 0`` disables the cap.
    """

    def __init__(self, filename, max_bytes, *args, **kwargs):
        self.max_bytes = max_bytes
        self._capped = False
        super().__init__(filename, *args, **kwargs)

    def emit(self, record):
        if self._capped:
            return
        # getsize reflects the size *before* this record (FileHandler flushes
        # after every emit), so we may overshoot the cap by at most one line.
        if self.max_bytes > 0:
            try:
                if os.path.getsize(self.baseFilename) >= self.max_bytes:
                    self._capped = True
                    self._write_cap_notice()
                    return
            except OSError:
                pass
        super().emit(record)

    def _write_cap_notice(self):
        """Leave a breadcrumb in the file explaining why it stopped growing."""
        try:
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(
                f"--- max_log_size of {self.max_bytes} bytes reached; "
                f"logging to this file stopped (delete/shrink it and restart "
                f"to resume) ---\n"
            )
            self.flush()
        except Exception:
            self.handleError(logging.LogRecord(
                "DODI", logging.ERROR, __file__, 0, "cap notice failed", None, None
            ))


def setup_file_logging(settings: dict):
    """Attach a capped file handler to the root logger if configured.

    Reads the ``debug`` block: ``log_file`` (None -> no-op) and ``max_log_size``
    (default "100M"). Relative paths resolve against the project directory. On
    any I/O error we warn and fall back to console-only logging rather than
    crashing the boot. Returns the handler, or None when disabled/failed.
    """
    debug_cfg = settings.get("debug") or {}
    log_file = debug_cfg.get("log_file")
    if not log_file:
        return None  # null/absent -> keep current (console/journal) behavior

    path = Path(log_file).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path

    raw_max = debug_cfg.get("max_log_size", DEFAULT_MAX_LOG_SIZE)
    try:
        max_bytes = parse_size(raw_max)
    except ValueError:
        logger.warning(
            "Invalid debug.max_log_size %r; defaulting to %s",
            raw_max, DEFAULT_MAX_LOG_SIZE,
        )
        max_bytes = parse_size(DEFAULT_MAX_LOG_SIZE)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = CappedFileHandler(str(path), max_bytes, encoding="utf-8")
    except OSError as e:
        logger.warning(
            "Could not open log file %s: %s; continuing without file logging",
            path, e,
        )
        return None

    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    logger.info("File logging enabled: %s (max_log_size=%s)", path, raw_max)
    return handler
