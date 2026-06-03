#!/usr/bin/env python3
"""Interactive diagnostic for the alarm 'click' on the WM8960 (ReSpeaker HAT).

Background
----------
`src/audio/output.py:_apply_fades` was added to kill a click. It was verified
against a synthetic buffer whose very first sample jumps 0 -> peak (a DC
discontinuity). A 15 ms ramp fixes *that* click.

But `assets/audio/morning.mp3` does not have that shape: it decodes to ~182 ms
of leading digital silence followed by a *gentle* attack. The 15 ms (661-sample)
fade therefore lands entirely inside the leading silence and ramps zeros into
zeros -- a complete no-op. So the click you still hear during the real alarm is
NOT the data click the fade was built for; it is the codec's analog
stream-activation pop, produced every time `sd.play()` opens the ALSA PCM.

This script proves that by ear. Each case plays a short clip; you note whether
you hear the click. Run it on the Pi with the speaker connected.

    venv/bin/python3 scripts/audio_click_test.py

Cases
-----
A  Pure silence via sd.play         -> if this clicks, the click is the
                                        hardware stream-open pop (no audio data
                                        involved => the fade can never fix it).
B  morning.mp3, CURRENT code path   -> reproduces the alarm exactly
   (load_audio -> scale_volume -> _apply_fades -> sd.play).
C  morning.mp3, silence TRIMMED     -> moves the fade onto the real onset.
   then faded                          If B clicks but C does NOT, the cause was
                                        the no-op fade. If both still click, the
                                        cause is the hardware pop (expected).
D  morning.mp3 via a PERSISTENT     -> the candidate fix: open one OutputStream,
   OutputStream that pre-rolls         pre-roll silence so the open-pop settles
   silence first                       during silence, then write the audio into
                                        the already-warm stream. If this is
                                        click-free, the fix is 'keep the stream
                                        open', not a bigger/longer fade.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Re-exec under the project venv if we were launched with a python that lacks
# the deps (e.g. plain `python3 scripts/...` when the active shell venv isn't
# this project's). Saves having to remember `venv/bin/python3`.
_VENV_PY = PROJECT_DIR / "venv" / "bin" / "python3"
try:
    import numpy  # noqa: F401
except ModuleNotFoundError:
    # venv/bin/python3 is usually a symlink to the same interpreter, so we
    # can't tell "am I the venv python?" by path. Guard with an env flag so we
    # re-exec at most once, then give up if numpy is still missing.
    if _VENV_PY.exists() and not os.environ.get("_AUDIO_CLICK_REEXEC"):
        os.environ["_AUDIO_CLICK_REEXEC"] = "1"
        os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])
    raise

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(PROJECT_DIR / "src"))

from alarm.audio_loader import load_audio  # noqa: E402
from audio.output import _apply_fades  # noqa: E402
from audio.volume import scale_volume  # noqa: E402

MORNING = PROJECT_DIR / "assets" / "audio" / "morning.mp3"
CLIP_SECONDS = 4.0  # the click is at the very start, so a short clip is enough


def _volume() -> int:
    try:
        cfg = json.loads((PROJECT_DIR / "config" / "settings.json").read_text())
        return int(cfg.get("audio", {}).get("volume", 100))
    except Exception:
        return 100


def _pause(msg: str) -> None:
    try:
        input(f"\n>>> {msg}\n    [Enter to play, Ctrl-C to quit] ")
    except EOFError:
        pass


def _describe(samples: np.ndarray, rate: int) -> None:
    peak = int(np.abs(samples).max()) or 1
    thr = max(2, peak // 1000)
    nz = int(np.argmax(np.abs(samples) > thr)) if np.any(np.abs(samples) > thr) else len(samples)
    f = int(rate * 15 / 1000.0)
    print(f"    decoded: {len(samples)} samples @ {rate} Hz, peak |x|={peak}")
    print(f"    first audible sample at index {nz} ({nz / rate * 1000:.0f} ms in)")
    print(f"    15 ms fade region = first {f} samples -> "
          f"{'ENTIRELY inside the leading silence (no-op!)' if nz >= f else 'overlaps real audio'}")


def case_A(rate: int) -> None:
    _pause("CASE A: one second of pure SILENCE via sd.play(). "
           "A click here = hardware stream-open pop.")
    silence = np.zeros(int(rate * 1.0), dtype=np.int16)
    sd.play(silence, rate)
    sd.wait()
    print("    (played 1 s of zeros)")


def case_B(samples: np.ndarray, rate: int, volume: int) -> None:
    _pause("CASE B: morning.mp3 via the CURRENT alarm code path (with the fade). "
           "This is what the alarm does today.")
    buf = scale_volume(samples.copy(), volume)
    buf = _apply_fades(buf, rate)
    sd.play(buf, rate)
    sd.wait()


def case_C(samples: np.ndarray, rate: int, volume: int) -> None:
    _pause("CASE C: morning.mp3 with leading silence TRIMMED, then faded. "
           "Now the fade actually lands on the audio onset.")
    peak = int(np.abs(samples).max()) or 1
    thr = max(2, peak // 1000)
    nz = int(np.argmax(np.abs(samples) > thr)) if np.any(np.abs(samples) > thr) else 0
    trimmed = samples[nz:].copy()
    buf = scale_volume(trimmed, volume)
    buf = _apply_fades(buf, rate)
    sd.play(buf, rate)
    sd.wait()


def case_D(samples: np.ndarray, rate: int, volume: int) -> None:
    _pause("CASE D: morning.mp3 via a PERSISTENT OutputStream that pre-rolls "
           "0.6 s of silence first (candidate fix: keep the codec powered).")
    buf = scale_volume(samples.copy(), volume)
    buf = _apply_fades(buf, rate)
    preroll = np.zeros(int(rate * 0.6), dtype=np.int16)
    stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16")
    stream.start()
    stream.write(preroll)   # open-pop happens here, during silence
    stream.write(buf)       # real audio into the already-warm stream
    stream.write(preroll)   # let it drain before we close
    stream.stop()
    stream.close()


def main() -> None:
    if not MORNING.exists():
        print(f"missing {MORNING}")
        sys.exit(1)

    loaded = load_audio(MORNING)
    if loaded is None:
        print("failed to decode morning.mp3")
        sys.exit(1)
    full, rate = loaded
    clip = full[: int(rate * CLIP_SECONDS)]
    volume = _volume()

    print(__doc__)
    print(f"output device : {sd.query_devices(kind='output')['name']}")
    print(f"config volume : {volume}")
    _describe(full, rate)

    case_A(rate)
    case_B(clip, rate, volume)
    case_C(clip, rate, volume)
    case_D(clip, rate, volume)

    print("\nInterpretation:")
    print("  A clicks            -> hardware stream-open pop; the fade cannot help.")
    print("  B clicks, C clean   -> the fade was a no-op on the leading silence.")
    print("  B & C click, D clean-> fix = keep the stream open / pre-roll silence,")
    print("                          not a longer fade.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sd.stop()
        print("\nstopped.")
