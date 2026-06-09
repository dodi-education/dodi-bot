"""Standalone alarm engine — decoupled from night mode.

A background daemon thread checks the `alarm` settings block each tick. When an
alarm is due (a one-time alarm overrides the weekly schedule), it rings: plays
the alarm audio on a loop, lights the screen with the clock, and waits for a
screen tap to dismiss. A one-time alarm deactivates itself after ringing
(persisted to settings.json).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from night_mode.display_power import set_brightness, set_power

from .audio_loader import load_audio
from .schedule import due_now

logger = logging.getLogger("DODI.alarm")

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
ASSETS_AUDIO_DIR = PROJECT_DIR / "assets" / "audio"

_TICK_SECONDS = 10.0


def _parse_fake_now_offset() -> Optional[timedelta]:
    """Honor DODI_FAKE_NOW (same as night mode) so alarms can be tested."""
    raw = os.environ.get("DODI_FAKE_NOW")
    if not raw:
        return None
    try:
        anchor = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return anchor - datetime.now()


def _coerce_pct(value, default: int) -> int:
    """Clamp a volume-percent setting to 0..100; fall back on bad/missing input."""
    try:
        return int(max(0, min(100, round(float(value)))))
    except (TypeError, ValueError):
        return int(max(0, min(100, default)))


def _coerce_float(value, default: float) -> float:
    """Coerce a settings value to a non-negative float; fall back on bad input."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


class AlarmState(Enum):
    IDLE = "idle"
    RINGING = "ringing"


class AlarmManager:
    """Fire alarms from the `alarm` settings block, independent of night mode."""

    def __init__(
        self,
        settings: dict,
        avatar,
        dodi,
        save_cb: Callable[[dict], bool],
        night_mode=None,
        time_provider: Optional[Callable[[], datetime]] = None,
    ):
        self._settings = settings
        self._avatar = avatar
        self._dodi = dodi
        self._save = save_cb
        self._night_mode = night_mode
        self._time_provider = time_provider or self._default_time_provider
        self._fake_offset = _parse_fake_now_offset()

        self._state = AlarmState.IDLE
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_fired: Optional[tuple] = None  # (date, "HH:MM") guards re-fire

    @property
    def _alarm_cfg(self) -> dict:
        return self._settings.get("alarm", {}) or {}

    def _default_time_provider(self) -> datetime:
        now = datetime.now()
        return now + self._fake_offset if self._fake_offset else now

    def is_ringing(self) -> bool:
        return self._state == AlarmState.RINGING

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="dodi-alarm")
        self._thread.start()
        logger.info("Alarm manager started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    # ------------------------------------------------------------------ core

    def _run(self) -> None:
        while not self._stop.is_set():
            now = self._time_provider()
            due = due_now(self._alarm_cfg, now)
            key = (now.date(), f"{now.hour:02d}:{now.minute:02d}")
            if due is not None and key != self._last_fired:
                self._last_fired = key
                try:
                    self._ring(due)
                except Exception as e:
                    logger.error(f"Error during alarm: {e}")
                    self._state = AlarmState.IDLE
            self._stop.wait(timeout=_TICK_SECONDS)

    def _ring(self, due: dict) -> None:
        logger.info(f"Alarm firing: {due.get('time')} (audio={due.get('audio')}, once={due.get('once')})")
        self._state = AlarmState.RINGING

        from display import DisplayMode

        # Take over the display: close settings if open, light the clock.
        if self._avatar is not None:
            try:
                self._avatar.close_settings()
            except Exception:
                pass
        set_power(True)
        set_brightness(100)
        if self._avatar is not None:
            self._avatar.set_mode(DisplayMode.CLOCK)
            # Pin the ringing alarm on the clock's next-alarm line: it stays
            # "Today" for the whole ring instead of flipping to "Tomorrow" once
            # the clock minute passes the alarm time.
            self._avatar.set_active_alarm({
                "time": due.get("time", "06:00"),
                "label": "Today",
                "once": bool(due.get("once")),
                "day": due.get("day"),
            })
            self._avatar.touch_event.clear()

        # Load + loop the audio (best-effort).
        samples = sample_rate = None
        if due.get("audio"):
            loaded = load_audio(ASSETS_AUDIO_DIR / due["audio"])
            if loaded is not None:
                samples, sample_rate = loaded
        ao = None
        try:
            ao = self._dodi._init_audio_output()
        except Exception as e:
            logger.warning(f"Could not init audio output for alarm: {e}")
        if samples is not None and ao is not None:
            cfg = self._alarm_cfg

            # Alarm volume is independent of the global audio.volume and is the
            # ceiling of the ascending ramp. Fall back to the global volume if
            # alarm.volume is absent (back-compat).
            default_vol = getattr(self._dodi, "volume", 100)
            alarm_volume = _coerce_pct(cfg.get("volume"), default=default_vol)
            ao.volume = alarm_volume

            # Ascending volume: rise from start_volume to alarm_volume over
            # `duration` seconds, then hold. Only ramp if it actually ascends.
            ramp = None
            asc = cfg.get("ascending_volume")
            if isinstance(asc, dict) and asc.get("active"):
                start_volume = _coerce_pct(asc.get("start_volume"), default=0)
                duration = _coerce_float(asc.get("duration"), default=0.0)
                if duration > 0 and start_volume < alarm_volume:
                    ramp = (start_volume, duration)

            try:
                ao.play_async(samples, sample_rate=sample_rate, loop=True, ramp=ramp)
            except Exception as e:
                logger.warning(f"Could not start alarm audio: {e}")

        try:
            self._wait_for_dismissal()
        finally:
            if self._avatar is not None:
                try:
                    self._avatar.set_active_alarm(None)
                except Exception:
                    pass
            if ao is not None:
                try:
                    ao.interrupt()
                except Exception:
                    pass
            # A one-time alarm rings once, then deactivates (and persists).
            if due.get("once"):
                one = self._alarm_cfg.get("one_time_alarm")
                if isinstance(one, dict):
                    one["active"] = False
                    try:
                        self._save(self._settings)
                    except Exception as e:
                        logger.warning(f"Could not persist one-time deactivation: {e}")
                    logger.info("One-time alarm fired -> deactivated")
            self._restore_display()
            self._state = AlarmState.IDLE

    def _wait_for_dismissal(self) -> None:
        """Block until the alarm is dismissed by a screen tap."""
        touch = self._avatar.touch_event if self._avatar is not None else None
        stop_touch = bool(self._alarm_cfg.get("stop_alarm_touch", True))
        while not self._stop.is_set():
            if stop_touch and touch is not None and touch.is_set():
                logger.info("Alarm dismissed by touch")
                touch.clear()
                return
            self._stop.wait(timeout=0.1)

    def _restore_display(self) -> None:
        """Hand the display back to whoever owns it after the alarm."""
        from display import DisplayMode
        if self._night_mode is not None and self._night_mode.is_active():
            # Night mode re-applies its CLOCK/BLANK + its own brightness.
            try:
                self._night_mode.reapply_display()
            except Exception:
                pass
        else:
            set_power(True)
            set_brightness(100)
            if self._avatar is not None:
                self._avatar.set_mode(DisplayMode.AVATAR)
