#!/usr/bin/env python3
"""Quick check for the startup playback stutter (and that the fix holds).

Exercises the SAME path the alarm uses (AudioOutput.play with loop=True + an
ascending ramp on assets/audio/morning.mp3), with NO UI running.

Root cause (confirmed): the Pi's 'ondemand' CPU governor downclocked into the
near-idle steady state of playback and the DVFS transition starved PortAudio's
callback, causing a one-off dropout a few seconds in. The fix pins the
'performance' governor for the duration of playback (audio.cpu_governor) and
trims the pre-playback work so it no longer reprocesses the whole clip.

Expected now: it should play cleanly. Watch the logs:
  CPU governor ondemand -> performance for playback   (boost engaged)
  (NO "Audio underrun during playback" warning)        (no dropout)
  CPU governor restored -> ondemand                    (boost released)

Usage:  ./venv/bin/python scripts/audio_stutter_diag.py [seconds]
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from alarm.audio_loader import load_audio  # noqa: E402
from audio import AudioOutput  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
MORNING = PROJECT_DIR / "assets" / "audio" / "morning.mp3"


def main() -> None:
    play_seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0

    loaded = load_audio(MORNING)
    if loaded is None:
        print(f"Could not load {MORNING}")
        return
    samples, sample_rate = loaded

    ao = AudioOutput(volume=80)
    ao.volume = 80
    # Mirror the alarm's ascending ramp so the path is identical.
    ramp = (5, 8.0)  # rise from 5% to 80% over 8s

    print(f"Playing morning.mp3 for {play_seconds:.0f}s (loop + ramp)...")
    ao.play_async(samples, sample_rate=sample_rate, loop=True, ramp=ramp)

    time.sleep(play_seconds)
    ao.interrupt()
    print("Done. Review the play()/UNDERFLOW log lines above.")


if __name__ == "__main__":
    main()
