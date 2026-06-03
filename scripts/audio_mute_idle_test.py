#!/usr/bin/env python3
"""Interactive test: can a hardware mute (Speaker volume -> 0) when idle give us
BOTH a silent speaker next to the bed AND a pop-free alarm/voice output?

Background
----------
`scripts/audio_click_test.py` showed the alarm 'click' is the codec's analog
stream-activation pop, fired every time `sd.play()` opens the ALSA PCM. Its
case D fix is "keep the stream open / pre-roll silence" so the open-pop settles
during silence and never recurs.

The catch: a *prewarmed* (always-open) WM8960 is not absolutely silent. With the
codec powered and the speaker amp live, you can hear a faint idle hiss/whine from
the speaker -- a problem when DODI sits right next to a bed. A previous test
confirmed prewarm-alone is audible.

This script tests the candidate that gets both properties at once:

    keep the codec warm (no open-pop)  +  hardware-mute the speaker amp while
    idle (`amixer 'Speaker' 0`)  ->  absolute silence between sounds, and the
    only transitions left are unmute-before-play / mute-after-play, both done
    while the signal is silence (so they should be pop-free too).

The judgement is by ear, next to the speaker, on the Pi:

    venv/bin/python3 scripts/audio_mute_idle_test.py

By default it drives the analog 'Speaker' control (0-127). Pass a different
control name to try the digital DAC instead, e.g. the path that mutes earlier in
the chain:

    venv/bin/python3 scripts/audio_mute_idle_test.py Playback

Cases
-----
R  Cold sd.play of pure silence       -> the reference open-pop you want gone.
                                          (No mute, no warm stream: today's path.)
1  Warm stream, NO mute, hold silence  -> reproduces "prewarm is not silent".
                                          Lean in: do you hear idle hiss/whine?
2  Warm stream, Speaker=0, hold silence-> the silence half of the fix. Lean in:
                                          is it now ABSOLUTELY silent?
3  Full cycle, INSTANT mute            -> idle(muted+warm) -> unmute -> play
                                          morning.mp3 -> re-mute. Any pop at the
                                          unmute or the re-mute? Is play clean?
4  Full cycle, RAMPED mute             -> same, but ramp Speaker 0<->target over
                                          ~150 ms. Compare with case 3: if 3 pops
                                          on the transition and 4 doesn't, ramp it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Re-exec under the project venv if launched with a python that lacks the deps
# (mirrors scripts/audio_click_test.py).
_VENV_PY = PROJECT_DIR / "venv" / "bin" / "python3"
try:
    import numpy  # noqa: F401
except ModuleNotFoundError:
    if _VENV_PY.exists() and not os.environ.get("_AUDIO_MUTE_REEXEC"):
        os.environ["_AUDIO_MUTE_REEXEC"] = "1"
        os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])
    raise

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(PROJECT_DIR / "src"))

from alarm.audio_loader import load_audio  # noqa: E402
from audio.output import _apply_fades  # noqa: E402
from audio.volume import scale_volume  # noqa: E402

MORNING = PROJECT_DIR / "assets" / "audio" / "morning.mp3"
CLIP_SECONDS = 4.0
IDLE_HOLD_SECONDS = 8.0  # how long each "lean in and listen" idle case holds


# --------------------------------------------------------------- amixer helpers

def _find_card() -> int:
    """Return the ALSA card index of the ReSpeaker/WM8960 voicecard (else 0)."""
    try:
        cards = Path("/proc/asound/cards").read_text()
    except OSError:
        return 0
    for line in cards.splitlines():
        # lines look like: " 0 [seeed2micvoicec]: ... "
        if "seeed" in line.lower() or "wm8960" in line.lower():
            m = re.match(r"\s*(\d+)\s", line)
            if m:
                return int(m.group(1))
    return 0


def _speaker_value(card: int, control: str) -> int | None:
    """Read the current raw value of `control` (first channel), or None."""
    try:
        out = subprocess.run(
            ["amixer", "-c", str(card), "sget", control],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    # e.g. "  Front Left: Playback 127 [100%] [6.00dB]"  or  "  Mono: 255 [..]"
    m = re.search(r"(?:Playback\s+)?(\d+)\s+\[\d+%\]", out)
    return int(m.group(1)) if m else None


def _set_speaker(card: int, control: str, value: int) -> None:
    subprocess.run(
        ["amixer", "-c", str(card), "sset", control, str(value)],
        capture_output=True, text=True, check=False,
    )


def _ramp_speaker(card: int, control: str, frm: int, to: int,
                  steps: int = 12, total_ms: float = 150.0) -> None:
    """Step the mixer from `frm` to `to` over ~total_ms (zipper-free unmute)."""
    sleep = (total_ms / 1000.0) / max(1, steps)
    for i in range(1, steps + 1):
        v = int(round(frm + (to - frm) * i / steps))
        _set_speaker(card, control, v)
        time.sleep(sleep)


# ------------------------------------------------------------------ warm stream

class WarmStream:
    """A persistent, already-open output stream feeding int16 silence on demand.

    Opening it pre-rolls silence so the codec's open-pop settles before any case
    relies on the stream being 'warm'. Writing zeros keeps ALSA running (the
    real prewarm fix would do exactly this between sounds).
    """

    def __init__(self, rate: int):
        self.rate = rate
        self._stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16")
        self._stream.start()
        self.feed_silence(0.6)  # pre-roll: absorb the one-time open-pop

    def feed_silence(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._stream.write(np.zeros(int(self.rate * seconds), dtype=np.int16))

    def feed(self, buf: np.ndarray) -> None:
        self._stream.write(buf)

    def close(self) -> None:
        try:
            self.feed_silence(0.2)
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


# ------------------------------------------------------------------------ cases

def _pause(msg: str) -> None:
    try:
        input(f"\n>>> {msg}\n    [Enter to run, Ctrl-C to quit] ")
    except EOFError:
        pass


def _countdown(label: str, seconds: float) -> None:
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        print(f"\r    {label}: {left:4.1f}s — lean in and listen ", end="", flush=True)
        time.sleep(0.1)
    print("\r    " + " " * 60 + "\r", end="", flush=True)


def case_R(rate: int) -> None:
    _pause("CASE R: cold sd.play() of 1 s pure silence — the reference open-pop "
           "(today's path, no warm stream, no mute). Memorise this click.")
    sd.play(np.zeros(int(rate * 1.0), dtype=np.int16), rate)
    sd.wait()
    print("    (played 1 s of zeros, cold)")


def case_1(rate: int, card: int, control: str, target: int) -> None:
    _pause(f"CASE 1: WARM stream, '{control}' at {target} (NOT muted), holding "
           "silence. This is prewarm-alone — expect a faint idle hiss/whine.")
    _set_speaker(card, control, target)
    ws = WarmStream(rate)
    try:
        _countdown("warm + unmuted, idle", IDLE_HOLD_SECONDS)
        ws.feed_silence(IDLE_HOLD_SECONDS)
    finally:
        ws.close()
    print("    Did you hear idle hiss? (expected: yes)")


def case_2(rate: int, card: int, control: str) -> None:
    _pause(f"CASE 2: WARM stream, '{control}'=0 (HARDWARE MUTE), holding silence. "
           "The silence half of the fix — should be ABSOLUTELY silent.")
    ws = WarmStream(rate)  # warms up while still audible-capable...
    try:
        _set_speaker(card, control, 0)  # ...then mute the amp
        _countdown("warm + MUTED, idle", IDLE_HOLD_SECONDS)
        ws.feed_silence(IDLE_HOLD_SECONDS)
    finally:
        ws.close()
    print("    Absolute silence? (this is the property we need next to the bed)")


def _full_cycle(rate: int, card: int, control: str, target: int,
                clip: np.ndarray, volume: int, ramped: bool) -> None:
    ws = WarmStream(rate)
    try:
        # idle: warm + muted (the silent resting state)
        _set_speaker(card, control, 0)
        ws.feed_silence(1.5)
        print("    [idle: warm + muted]")

        # an output arrives -> unmute (on silence), then play
        if ramped:
            _ramp_speaker(card, control, 0, target)
        else:
            _set_speaker(card, control, target)
        print(f"    [unmute {'(ramped)' if ramped else '(instant)'} -> play]")
        buf = _apply_fades(scale_volume(clip.copy(), volume), rate)
        ws.feed(buf)
        ws.feed_silence(0.3)  # let the faded tail drain to true zero

        # output done -> re-mute (on silence)
        if ramped:
            _ramp_speaker(card, control, target, 0)
        else:
            _set_speaker(card, control, 0)
        print(f"    [re-mute {'(ramped)' if ramped else '(instant)'} -> idle]")
        ws.feed_silence(1.0)
    finally:
        ws.close()


def case_3(rate: int, card: int, control: str, target: int,
           clip: np.ndarray, volume: int) -> None:
    _pause("CASE 3: full cycle, INSTANT mute. idle(warm+muted) -> unmute -> play "
           "morning.mp3 -> re-mute. Listen for a pop at unmute AND at re-mute.")
    _full_cycle(rate, card, control, target, clip, volume, ramped=False)


def case_4(rate: int, card: int, control: str, target: int,
           clip: np.ndarray, volume: int) -> None:
    _pause("CASE 4: full cycle, RAMPED mute (~150 ms). Same as case 3 but the "
           "Speaker volume is ramped 0<->target. Compare: did the ramp kill a "
           "transition pop that case 3 had?")
    _full_cycle(rate, card, control, target, clip, volume, ramped=True)


# -------------------------------------------------------------------------- main

def _volume() -> int:
    try:
        cfg = json.loads((PROJECT_DIR / "config" / "settings.json").read_text())
        return int(cfg.get("audio", {}).get("volume", 100))
    except Exception:
        return 100


def main() -> None:
    control = sys.argv[1] if len(sys.argv) > 1 else "Speaker"
    card = _find_card()

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

    original = _speaker_value(card, control)
    if original is None:
        print(f"could not read mixer control '{control}' on card {card}.")
        print("List controls with:  amixer -c %d scontrols" % card)
        sys.exit(1)
    target = original  # unmute back to wherever the system had it

    print(__doc__)
    print(f"output device : {sd.query_devices(kind='output')['name']}")
    print(f"mixer card    : {card}   control : '{control}'")
    print(f"unmute target : {target}   (current value, restored on exit)")
    print(f"config volume : {volume} (software, applied to the clip)")

    try:
        case_R(rate)
        case_1(rate, card, control, target)
        case_2(rate, card, control)
        case_3(rate, card, control, target, clip, volume)
        case_4(rate, card, control, target, clip, volume)
    finally:
        _set_speaker(card, control, target)  # never leave the speaker muted
        print(f"\nrestored '{control}' -> {target}.")

    print("\nInterpretation:")
    print("  1 hisses, 2 silent        -> hardware mute delivers the bedside silence")
    print("                               prewarm-alone could not.")
    print("  3 clean (no pops)         -> unmute-on-silence / mute-on-silence is")
    print("                               pop-free: ship instant mute.")
    print("  3 pops, 4 clean           -> the mute transition itself zips; ramp it")
    print("                               (~150 ms) as in case 4.")
    print("  R pops, 2 & 3 silent/clean-> the whole approach works: warm codec +")
    print("                               idle hardware-mute around each output.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sd.stop()
        # best-effort restore even on Ctrl-C
        try:
            _set_speaker(_find_card(), sys.argv[1] if len(sys.argv) > 1 else "Speaker",
                         127)
        except Exception:
            pass
        print("\nstopped (restored Speaker).")
