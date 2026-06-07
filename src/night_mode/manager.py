"""Night mode orchestrator (display-only).

A background daemon thread evaluates the schedule each tick. When entering a
night-mode session it stops the microphone capture and switches the display
(clock or blank) for the session's timespan. Alarms are NOT part of night mode
anymore — they live in the separate `src/alarm` engine.

DODI's control loop gates on `is_active()` so night-mode behaviour is fully
orthogonal to the DEAF/NORMAL conversation state.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional

from .display_power import set_brightness, set_power
from .schedule import Block, next_session_for

logger = logging.getLogger("DODI.night_mode")

_TICK_SECONDS = 5.0


class NightModeState(Enum):
    OFF = "off"
    IDLE_SESSION = "idle_session"


def _parse_fake_now_offset() -> Optional[timedelta]:
    """If DODI_FAKE_NOW is set, return an offset so wall-clock time still flows.

    The env var anchors the *initial* clock reading to a specific moment; from
    that point on, time advances normally. This makes session/alarm transitions
    actually trigger during a test run.
    """
    raw = os.environ.get("DODI_FAKE_NOW")
    if not raw:
        return None
    try:
        anchor = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.warning(f"Invalid DODI_FAKE_NOW={raw!r}; expected 'YYYY-MM-DD HH:MM:SS'")
        return None
    offset = anchor - datetime.now()
    logger.info(f"DODI_FAKE_NOW active: anchored to {anchor} (offset={offset})")
    return offset


class NightModeManager:
    """Drive night-mode entry/exit, display state, and alarm playback."""

    def __init__(
        self,
        config: dict,
        avatar,
        dodi,
        time_provider: Optional[Callable[[], datetime]] = None,
    ):
        self._config = config or {}
        self._avatar = avatar
        self._dodi = dodi
        self._time_provider = time_provider or self._default_time_provider

        self._enabled = bool(self._config.get("enabled", False))
        self._deactivate_sst = bool(self._config.get("deactivate_sst", True))
        self._schedule = self._config.get("schedule", {}) or {}

        display_cfg = self._config.get("display")
        if display_cfg is None:
            # Back-compat shim for the previous deactivate_display flag.
            if self._config.get("deactivate_display"):
                display_cfg = {"mode": "deactivated", "brightness": 0}
            else:
                display_cfg = {"mode": "time", "brightness": 100}
        self._display_mode = display_cfg.get("mode", "time")
        self._brightness = int(display_cfg.get("brightness", 100))

        self._state = NightModeState.OFF
        self._state_changed = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Capture the fake-time offset once so wall-clock time still advances.
        self._fake_offset: Optional[timedelta] = _parse_fake_now_offset()

    def _default_time_provider(self) -> datetime:
        now = datetime.now()
        if self._fake_offset is not None:
            return now + self._fake_offset
        return now

    def is_active(self) -> bool:
        return self._state != NightModeState.OFF

    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="dodi-night-mode"
        )
        self._thread.start()
        logger.info("Night mode manager started")

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable night mode at runtime (live, from the settings UI).

        Enabling (re)starts the scheduler thread; disabling stops it. A disable
        mid-session lets the worker's own cleanup restore the display (full
        brightness, avatar mode) as it unwinds, so no display work happens here.
        Safe to call from any thread; the worker is responsive to ``_stop`` so the
        join returns promptly.
        """
        enabled = bool(enabled)
        if enabled:
            self._enabled = True
            self._stop.clear()
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="dodi-night-mode"
                )
                self._thread.start()
                logger.info("Night mode enabled at runtime")
        else:
            if self._thread is None and not self._enabled:
                return
            self._enabled = False
            self._stop.set()
            t, self._thread = self._thread, None
            if t is not None:
                t.join(timeout=3.0)
            self._state_changed.set()   # wake any wait_until_inactive()
            logger.info("Night mode disabled at runtime")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        # Always restore the display on shutdown so the user isn't left dark.
        try:
            set_power(True)
            set_brightness(100)
        except Exception:
            pass
        if self._avatar is not None:
            from display import DisplayMode
            try:
                self._avatar.set_mode(DisplayMode.AVATAR)
            except Exception:
                pass
        logger.info("Night mode manager stopped")

    def wait_until_inactive(self, poll: float = 0.5) -> None:
        """Block the caller until night mode exits.

        The main loop calls this when `is_active()` returns true; it must not
        process wake words while we own audio_input/STT.
        """
        while self.is_active() and not self._stop.is_set():
            self._state_changed.wait(timeout=poll)
            self._state_changed.clear()

    # ------------------------------------------------------------------ core

    def _run(self) -> None:
        while not self._stop.is_set():
            now = self._time_provider()
            session = next_session_for(now, self._schedule)
            if session is None:
                # No future session: sleep a while, then re-check.
                self._stop.wait(timeout=60.0)
                continue

            if now < session.start_dt:
                wait_s = (session.start_dt - now).total_seconds()
                # Cap waits so config edits / clock jumps are picked up promptly.
                self._stop.wait(timeout=min(wait_s, 60.0))
                continue

            # We're inside the session.
            self._enter_session(session)

    def _set_state(self, new_state: NightModeState) -> None:
        if self._state == new_state:
            return
        logger.info(f"Night mode state: {self._state.value} -> {new_state.value}")
        self._state = new_state
        self._state_changed.set()

    def _apply_display(self) -> None:
        """Switch avatar mode + backlight to match the configured display mode."""
        from display import DisplayMode
        if self._display_mode == "deactivated":
            if self._avatar is not None:
                self._avatar.set_mode(DisplayMode.BLANK)
            set_brightness(0)
            set_power(False)
        else:  # "time"
            if self._avatar is not None:
                self._avatar.set_mode(DisplayMode.CLOCK)
            set_power(True)
            set_brightness(self._brightness)

    def reapply_display(self) -> None:
        """Re-assert the night-mode display (e.g. after an alarm lit the screen)."""
        if self.is_active():
            self._apply_display()

    def _restore_display(self) -> None:
        from display import DisplayMode
        set_power(True)
        set_brightness(100)
        if self._avatar is not None:
            self._avatar.set_mode(DisplayMode.AVATAR)

    def _stop_main_mic(self) -> None:
        """Stop the control loop's microphone capture during a night session."""
        if not self._deactivate_sst:
            return
        try:
            ai = self._dodi._audio_input
            if ai is not None:
                ai.stop()
        except Exception as e:
            logger.warning(f"Could not stop audio input: {e}")

    def _enter_session(self, session: Block) -> None:
        """Hold inside a night-mode session until its end_dt, showing the display."""
        logger.info(f"Entering night mode (until {session.end_dt})")
        self._set_state(NightModeState.IDLE_SESSION)
        self._stop_main_mic()
        self._apply_display()
        if self._avatar is not None:
            self._avatar.touch_event.clear()

        try:
            while not self._stop.is_set():
                now = self._time_provider()
                if now >= session.end_dt:
                    break
                # Poll so NTP jumps and stop() are picked up quickly.
                self._stop.wait(timeout=_TICK_SECONDS)
        finally:
            self._restore_display()
            self._set_state(NightModeState.OFF)
