"""Tests for the two-mode (DEAF/NORMAL) control logic.

Covers the pure, hardware-free pieces of the rewrite:
- double-tap classification timing
- the inactivity timer
- DEAF<->NORMAL transitions (mic on/off + provider connect/disconnect)
"""

import asyncio
import sys
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


# --------------------------------------------------------------- double-tap

def test_classify_tap_first_tap_is_single():
    from display.avatar import _classify_tap, DOUBLE_TAP_WINDOW
    kind, last = _classify_tap(0.0, 100.0, DOUBLE_TAP_WINDOW)
    assert kind == "single"
    assert last == 100.0


def test_classify_tap_second_within_window_is_double():
    from display.avatar import _classify_tap, DOUBLE_TAP_WINDOW
    kind, last = _classify_tap(100.0, 100.0 + DOUBLE_TAP_WINDOW - 0.01, DOUBLE_TAP_WINDOW)
    assert kind == "double"
    assert last == 0.0  # reset so a triple-tap can't double-fire


def test_classify_tap_slow_second_is_single():
    from display.avatar import _classify_tap, DOUBLE_TAP_WINDOW
    kind, last = _classify_tap(100.0, 100.0 + DOUBLE_TAP_WINDOW + 0.5, DOUBLE_TAP_WINDOW)
    assert kind == "single"
    assert last == 100.0 + DOUBLE_TAP_WINDOW + 0.5


def test_classify_tap_triple_does_not_double_twice():
    from display.avatar import _classify_tap, DOUBLE_TAP_WINDOW
    w = DOUBLE_TAP_WINDOW
    # tap1 -> single
    k1, last = _classify_tap(0.0, 10.0, w)
    # tap2 (fast) -> double, resets last to 0.0
    k2, last = _classify_tap(last, 10.1, w)
    # tap3 (fast again) -> single, because last was reset
    k3, last = _classify_tap(last, 10.2, w)
    assert (k1, k2, k3) == ("single", "double", "single")


# ------------------------------------------------------------ inactivity timer

def test_inactivity_timer_expires_after_timeout():
    from conversation import InactivityTimer
    now = [100.0]
    timer = InactivityTimer(10.0, clock=lambda: now[0])

    assert not timer.expired()  # never started -> not expired
    timer.mark()
    assert not timer.expired()
    now[0] = 109.9
    assert not timer.expired()
    now[0] = 110.0
    assert timer.expired()


def test_inactivity_timer_reset_on_activity():
    from conversation import InactivityTimer
    now = [0.0]
    timer = InactivityTimer(5.0, clock=lambda: now[0])
    timer.mark()
    now[0] = 4.9
    timer.mark()          # activity just before expiry
    now[0] = 9.0          # 4.1s since last activity
    assert not timer.expired()
    assert timer.remaining() > 0


def test_inactivity_timer_clear_disables():
    from conversation import InactivityTimer
    now = [0.0]
    timer = InactivityTimer(1.0, clock=lambda: now[0])
    timer.mark()
    timer.clear()
    now[0] = 1000.0
    assert not timer.expired()
    assert timer.remaining() == 1.0


# --------------------------------------------------------------- mode changes

class _FakeAudioInput:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.cleared = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def clear_queue(self):
        self.cleared = True

    def read(self, timeout=0.1):
        return None


class _FakeProvider:
    def __init__(self):
        self.started = False
        self.stopped = False
        self._connected = False
        self.is_speaking = False

        self.sent = []

    async def start_session(self, system_prompt=None, on_transcript=None, on_end=None):
        self.started = True
        self._connected = True

    async def stop_session(self):
        self.stopped = True
        self._connected = False

    async def send_audio(self, chunk):
        self.sent.append(chunk)

    @property
    def is_connected(self):
        return self._connected


class _FakeAvatar:
    def __init__(self):
        self.deaf = None
        self.double_tap_event = threading.Event()

    def set_deaf(self, value):
        self.deaf = value


def _make_dodi(monkeypatch, provider):
    import main
    settings = {
        "audio": {"sample_rate": 16000},
        "streaming": {"enabled": True, "active": "gemini", "gemini": {}},
        "conversation": {"timeout_seconds": 10.0},
    }
    dodi = main.DODI(settings)
    dodi._persona = "test persona"  # skip persona file read
    dodi._audio_input = _FakeAudioInput()
    dodi._avatar = _FakeAvatar()
    monkeypatch.setattr(main, "is_provider_available", lambda name: True)
    monkeypatch.setattr(main, "create_streaming_provider", lambda *a, **k: provider)
    return main, dodi


def test_enter_normal_starts_mic_and_connects(monkeypatch):
    provider = _FakeProvider()
    main, dodi = _make_dodi(monkeypatch, provider)

    asyncio.run(dodi._enter_normal())

    assert dodi._mode is main.Mode.NORMAL
    assert dodi._audio_input.started and dodi._audio_input.cleared
    assert provider.started
    assert dodi._provider is provider
    assert dodi._avatar.deaf is False


def test_enter_deaf_stops_mic_and_disconnects(monkeypatch):
    provider = _FakeProvider()
    main, dodi = _make_dodi(monkeypatch, provider)

    asyncio.run(dodi._enter_normal())
    asyncio.run(dodi._enter_deaf())

    assert dodi._mode is main.Mode.DEAF
    assert provider.stopped
    assert dodi._audio_input.stopped  # mic truly off — privacy guarantee
    assert dodi._provider is None
    assert dodi._avatar.deaf is True


def test_enter_normal_stays_deaf_when_provider_unavailable(monkeypatch):
    import main
    dodi = main.DODI({"streaming": {"enabled": True, "active": "gemini"}})
    dodi._avatar = _FakeAvatar()
    monkeypatch.setattr(main, "is_provider_available", lambda name: False)

    asyncio.run(dodi._enter_normal())

    assert dodi._mode is main.Mode.DEAF
    assert dodi._provider is None


# ----------------------------------------- inactivity timer resets on real turns

def _pump_with(monkeypatch, chunk, is_speaking=False):
    provider = _FakeProvider()
    provider._connected = True
    provider.is_speaking = is_speaking
    _, dodi = _make_dodi(monkeypatch, provider)
    dodi._provider = provider
    dodi._audio_input.read = lambda timeout=0.05: chunk
    dodi._timer.clear()  # start from "no activity"

    async def run():
        loop = asyncio.get_running_loop()
        await dodi._pump_once(loop)

    asyncio.run(run())
    return dodi, provider


def test_pump_does_not_reset_timer_on_raw_mic(monkeypatch):
    # Raw mic audio (even loud) must NOT reset the timeout — ambient noise would
    # keep the session alive forever. Only real turns reset it.
    loud = b"\x40\x1f" * 4000  # int16 ~8000 amplitude
    dodi, provider = _pump_with(monkeypatch, loud, is_speaking=False)
    assert provider.sent == [loud]                # still forwarded to the model
    assert dodi._timer._last_activity is None     # timer NOT reset


def test_pump_resets_timer_while_ai_speaking(monkeypatch):
    # While the AI speaks: keep the session alive and skip the mic (echo guard).
    chunk = b"\x00\x10" * 4000
    dodi, provider = _pump_with(monkeypatch, chunk, is_speaking=True)
    assert provider.sent == []                    # not forwarded while speaking
    assert dodi._timer._last_activity is not None  # AI speech reset the timer


def test_on_transcript_resets_timer(monkeypatch):
    _, dodi = _make_dodi(monkeypatch, _FakeProvider())
    dodi._timer.clear()
    dodi._on_transcript("hallo")
    assert dodi._timer._last_activity is not None


def test_on_turn_end_resets_timer(monkeypatch):
    _, dodi = _make_dodi(monkeypatch, _FakeProvider())
    dodi._timer.clear()
    dodi._on_turn_end()
    assert dodi._timer._last_activity is not None


# ------------------------------------------------ audio streaming lifecycle

def test_audio_output_stream_stop_mid_playback():
    """Regression guard: stopping the stream while it's playing must not crash.

    The old writer-thread design segfaulted (PortAudio accessed from two
    threads) when a double-tap stopped playback mid-response. The callback-based
    stream must tear down cleanly. (No-ops safely if no audio device.)
    """
    from audio import AudioOutput
    silence = b"\x00\x00" * 2400  # 0.1s @ 24k mono, inaudible
    for _ in range(3):
        ao = AudioOutput(sample_rate=24000, volume=50)
        ao.start_stream(24000)
        for _ in range(10):
            ao.feed_stream(silence)
        ao.stop_stream()
        assert not ao.is_playing()
