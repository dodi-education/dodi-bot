"""Hardware mute for the WM8960 speaker amp (Seeed/Keyestudio ReSpeaker HAT).

Why this exists
---------------
The codec fires an analog "pop" every time the ALSA PCM is opened or closed --
it is a power/stream-activation transient, not anything in the audio data, so no
software fade can remove it. Keeping the stream permanently open (prewarm) avoids
the pop but leaves the speaker faintly hissing, which is unacceptable for a
device sitting next to a bed.

By-ear testing (scripts/audio_mute_idle_test.py) showed the fix: drive the analog
'Speaker' mixer to 0 *across* the open/close window. The pop lands in a muted amp
(silent), and the device idles dead-silent because a *closed* stream lets ASoC
power the amp down. Unmute/mute are performed while the signal is silence
(preroll / faded tail), which is itself pop-free; an instant set was enough -- no
ramp needed.

This module wraps the one mixer control via `amixer`. Every operation is
best-effort: on a machine without the control (CI, a dev laptop) `available` is
False and all ops are silent no-ops, so AudioOutput keeps working unchanged.
"""

from __future__ import annotations

import atexit
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_MUTE_VALUE = 0


class SpeakerMute:
    """Mute/unmute the speaker output PGA to bracket the codec's open/close pop.

    The "unmute" target is the control's max (full analog gain); loudness is
    handled separately by the software volume applied to the PCM. Mute is a
    binary gate, not a volume control.
    """

    # Registered once per process: restore whatever the speaker was before we
    # first touched it, so exiting (or a test run) never leaves the Pi muted.
    _restore_registered = False
    _restore_done = False
    _restore_card: Optional[int] = None
    _restore_control: Optional[str] = None
    _restore_value: Optional[int] = None

    def __init__(self, control: str = "Speaker", enabled: bool = True):
        self.control = control
        self.enabled = enabled
        self.card: Optional[int] = None
        self.unmute_value: Optional[int] = None

        if not enabled:
            return

        self.card = self._find_card()
        self.unmute_value = self._read_max()
        if self.unmute_value is None:
            logger.info(
                "SpeakerMute: mixer control %r not found on card %s; "
                "hardware mute disabled (playback will work, pop unmanaged).",
                control, self.card,
            )
            self.enabled = False
            return

        # Capture the pre-mute value exactly once, before anything mutes, so the
        # atexit restore puts the system back to where the user had it.
        cls = SpeakerMute
        if cls._restore_value is None:
            current = self._read_value()
            cls._restore_value = current if current else self.unmute_value
            cls._restore_card = self.card
            cls._restore_control = self.control
        if not cls._restore_registered:
            atexit.register(cls._restore_on_exit)
            cls._restore_registered = True

    @property
    def available(self) -> bool:
        return self.enabled and self.unmute_value is not None

    # ----------------------------------------------------------------- public

    def mute(self) -> None:
        """Silence the speaker amp (the idle/resting state)."""
        if self.available:
            self._set(_MUTE_VALUE)

    def unmute(self) -> None:
        """Restore full analog gain (loudness comes from the software volume)."""
        if self.available:
            self._set(self.unmute_value)

    # alias: reads better at call sites that establish the resting state
    idle = mute

    # ---------------------------------------------------------------- internals

    def _set(self, value: int) -> None:
        self._amixer(["sset", self.control, str(value)])

    def _amixer(self, args: List[str]) -> str:
        try:
            return subprocess.run(
                ["amixer", "-c", str(self.card), *args],
                capture_output=True, text=True, check=False,
            ).stdout
        except FileNotFoundError:
            # No amixer at all -> give up quietly for the rest of the process.
            self.enabled = False
            return ""

    def _read_value(self) -> Optional[int]:
        # e.g. "  Front Left: Playback 127 [100%] [6.00dB]"
        m = re.search(r"(?:Playback\s+)?(\d+)\s+\[\d+%\]",
                      self._amixer(["sget", self.control]))
        return int(m.group(1)) if m else None

    def _read_max(self) -> Optional[int]:
        # e.g. "  Limits: Playback 0 - 127"  /  "  Limits: 0 - 255"
        m = re.search(r"Limits:\s*(?:Playback\s*)?\d+\s*-\s*(\d+)",
                      self._amixer(["sget", self.control]))
        return int(m.group(1)) if m else None

    @staticmethod
    def _find_card() -> int:
        """ALSA card index of the ReSpeaker/WM8960 voicecard (default 0)."""
        try:
            cards = Path("/proc/asound/cards").read_text()
        except OSError:
            return 0
        for line in cards.splitlines():
            if "seeed" in line.lower() or "wm8960" in line.lower():
                m = re.match(r"\s*(\d+)\s", line)
                if m:
                    return int(m.group(1))
        return 0

    @classmethod
    def _restore_on_exit(cls) -> None:
        if cls._restore_done or cls._restore_value is None:
            return
        cls._restore_done = True
        try:
            subprocess.run(
                ["amixer", "-c", str(cls._restore_card), "sset",
                 cls._restore_control, str(cls._restore_value)],
                capture_output=True, text=True, check=False,
            )
        except Exception:
            pass
