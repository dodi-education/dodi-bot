"""Hermetic tests for AudioOutput's hardware-mute bracketing and interrupt path.

These run without an audio device or mixer: sounddevice and the speaker mute are
both replaced with fakes, so we test the control flow (mute ordering, no deadlock
on interrupt) deterministically. The on-hardware, by-ear behaviour lives in
scripts/audio_mute_idle_test.py.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import audio.output as output  # noqa: E402
from audio.output import (  # noqa: E402
    AudioOutput, _apply_fades, _silence, _PREROLL_S, _TAIL_S,
)
from audio.volume import scale_volume  # noqa: E402


class _RecordingMute:
    """Stand-in for SpeakerMute that records the order of mute/unmute calls."""

    def __init__(self, control="Speaker", enabled=True):
        self.calls = []
        self.available = True

    def mute(self):
        self.calls.append("mute")

    def unmute(self):
        self.calls.append("unmute")

    idle = mute


class _FakeStream:
    """active is True for `reads` polls, then False (simulates a finite buffer)."""

    def __init__(self, reads):
        self._reads = reads

    @property
    def active(self):
        if self._reads <= 0:
            return False
        self._reads -= 1
        return True


class _FakeSD:
    def __init__(self, reads):
        self._reads = reads
        self.play_count = 0
        self.stop_count = 0
        self.bufs = []  # every buffer handed to sd.play (copied)
        self._stream = _FakeStream(0)

    def play(self, buf, rate):
        self.play_count += 1
        self.bufs.append(np.asarray(buf).copy())
        self._stream = _FakeStream(self._reads)

    def get_stream(self):
        return self._stream

    def stop(self):
        self.stop_count += 1
        self._stream._reads = 0  # make the stream go inactive


@pytest.fixture
def fake_audio(monkeypatch):
    """Patch sounddevice + SpeakerMute; return (make_output, fake_sd_holder)."""
    monkeypatch.setattr(output, "SpeakerMute", _RecordingMute)
    holder = {}

    def make_output(reads):
        fake = _FakeSD(reads)
        holder["sd"] = fake
        monkeypatch.setattr(output, "sd", fake)
        return AudioOutput(sample_rate=44100, volume=50)

    return make_output, holder


def test_play_brackets_with_mute_unmute_mute(fake_audio):
    """A one-shot play mutes, unmutes during the preroll, then mutes at the end,
    and stops the stream exactly once (from the play thread, no interrupt)."""
    make_output, holder = fake_audio
    ao = make_output(reads=12)  # ~0.6s > the 0.25s unmute delay
    ok = ao.play(np.zeros(4410, dtype=np.int16))
    assert ok is True
    # idle() at construction + mute before open, then unmute, then final mute.
    assert ao._mute.calls[0] == "mute"          # construction idle()
    assert "unmute" in ao._mute.calls
    assert ao._mute.calls.index("unmute") < len(ao._mute.calls) - 1
    assert ao._mute.calls[-1] == "mute"         # muted at idle afterwards
    assert holder["sd"].stop_count == 1
    assert not ao.is_playing()


def test_interrupt_loop_terminates_without_deadlock(fake_audio):
    """Regression: interrupting a looping playback must not hang.

    The play thread and interrupt() must not both call sd.stop() (PortAudio's
    global stream is not thread-safe and deadlocks). interrupt() owns the single
    stop; the play thread only watches the flag and exits.
    """
    make_output, holder = fake_audio
    ao = make_output(reads=10_000)  # effectively endless until stopped
    ao.play_async(np.zeros(4410, dtype=np.int16), loop=True)
    time.sleep(0.4)
    assert ao.is_playing()

    ao.interrupt()
    # The play thread must have exited (join in interrupt has a timeout; assert
    # it really finished rather than merely timing out).
    assert ao._play_thread is not None and not ao._play_thread.is_alive()
    assert not ao.is_playing()
    assert ao._mute.calls[-1] == "mute"         # left muted
    assert holder["sd"].stop_count == 1         # ONLY interrupt() stopped it


def test_repeated_interrupt_never_hangs(fake_audio):
    """Hammer the interrupt race: every cycle's thread must terminate."""
    make_output, _ = fake_audio
    ao = make_output(reads=10_000)
    for _ in range(5):
        ao.play_async(np.zeros(4410, dtype=np.int16), loop=True)
        time.sleep(0.05)
        ao.interrupt()
        assert not ao._play_thread.is_alive()


# ------------------------------------------------------ ascending volume (ramp)

def test_play_ramp_rises_then_holds(fake_audio):
    """A looped ramp produces buffers whose peak rises geometrically, then holds
    at the steady-state (self.volume) level once the ramp duration elapses."""
    make_output, holder = fake_audio
    ao = make_output(reads=1)                 # ~1 buffer per ~50ms
    rate = 1000
    tone = np.full(rate, 10000, dtype=np.int16)   # 1s of full-scale audio/buffer
    ao.play_async(tone, sample_rate=rate, loop=True, ramp=(10, 4.0))
    time.sleep(0.6)
    ao.interrupt()

    peaks = [int(np.abs(b).max()) for b in holder["sd"].bufs]
    assert len(peaks) >= 5
    # Within the 4s ramp the peak rises every 1s buffer (geometric, monotonic).
    assert peaks[0] < peaks[1] < peaks[2] < peaks[3]
    # Past the ramp it settles at the 50% steady state (~5000) and never exceeds it.
    assert max(peaks) <= 5001
    assert any(abs(p - 5000) <= 2 for p in peaks[4:])


def test_play_ramp_none_matches_constant_volume(fake_audio):
    """ramp=None must be byte-identical to today's constant-volume buffer."""
    make_output, holder = fake_audio
    ao = make_output(reads=2)
    rate = 1000
    tone = np.full(rate, 10000, dtype=np.int16)
    ok = ao.play(tone, sample_rate=rate, loop=False)
    assert ok is True

    audio_max = _apply_fades(scale_volume(tone, 50), rate)
    expected = np.concatenate([
        _silence(int(rate * _PREROLL_S), audio_max),
        audio_max,
        _silence(int(rate * _TAIL_S), audio_max),
    ])
    bufs = holder["sd"].bufs
    assert len(bufs) == 1
    assert np.array_equal(bufs[0], expected)


def test_interrupt_with_ramp_terminates(fake_audio):
    """The ramp must not break the interrupt/deadlock invariant."""
    make_output, holder = fake_audio
    ao = make_output(reads=10_000)
    ao.play_async(np.full(1000, 8000, dtype=np.int16),
                  sample_rate=1000, loop=True, ramp=(10, 5.0))
    time.sleep(0.2)
    ao.interrupt()
    assert not ao._play_thread.is_alive()
    assert not ao.is_playing()
    assert ao._mute.calls[-1] == "mute"
    assert holder["sd"].stop_count == 1
