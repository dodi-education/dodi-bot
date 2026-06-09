"""Tests for the standalone alarm engine: schedule logic + settings persistence."""

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from alarm.schedule import next_ring, due_now  # noqa: E402
from alarm.manager import AlarmManager  # noqa: E402
import settings_store  # noqa: E402


# 2026-06-03 is a Wednesday.
WED_0500 = datetime(2026, 6, 3, 5, 0)
WED_0700 = datetime(2026, 6, 3, 7, 0)


def _cfg(one_time=None, schedule=None):
    return {
        "one_time_alarm": one_time or {"active": False, "time": "06:00", "audio": "m.mp3"},
        "schedule": schedule or {},
    }


def _all_days(active=True, time="06:00"):
    return {d: {"active": active, "time": time, "audio": "m.mp3"}
            for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


# ---------------------------------------------------------------- next_ring

def test_next_ring_one_time_today():
    cfg = _cfg(one_time={"active": True, "time": "07:00", "audio": "m.mp3"})
    nr = next_ring(cfg, WED_0500)  # 07:00 is still ahead
    assert nr["once"] is True and nr["label"] == "Today" and nr["time"] == "07:00"


def test_next_ring_one_time_tomorrow_when_passed():
    cfg = _cfg(one_time={"active": True, "time": "06:00", "audio": "m.mp3"})
    nr = next_ring(cfg, WED_0700)  # 06:00 already passed
    assert nr["once"] is True and nr["label"] == "Tomorrow"


def test_next_ring_one_time_today_while_ringing():
    # When the one-time alarm is actively triggering, alarm time == now;
    # it must read "Today", not "Tomorrow".
    cfg = _cfg(one_time={"active": True, "time": "07:00", "audio": "m.mp3"})
    nr = next_ring(cfg, WED_0700)  # 07:00 == now (the ringing minute)
    assert nr["once"] is True and nr["label"] == "Today"


def test_next_ring_schedule_today():
    cfg = _cfg(schedule=_all_days(time="06:00"))
    nr = next_ring(cfg, WED_0500)
    assert nr["once"] is False and nr["label"] == "Today" and nr["day"] == "Wed"


def test_next_ring_skips_today_when_passed():
    cfg = _cfg(schedule=_all_days(time="06:00"))
    nr = next_ring(cfg, WED_0700)  # Wed passed -> Thursday
    assert nr["label"] == "Tomorrow" and nr["day"] == "Thu"


def test_next_ring_skips_inactive_days():
    sched = _all_days(active=False)
    sched["Fri"] = {"active": True, "time": "06:00", "audio": "m.mp3"}
    nr = next_ring(_cfg(schedule=sched), WED_0700)
    assert nr["day"] == "Fri" and nr["label"] == "Friday"


def test_next_ring_none_when_nothing_active():
    assert next_ring(_cfg(schedule=_all_days(active=False)), WED_0500) is None


def test_next_ring_one_time_overrides_schedule():
    cfg = _cfg(one_time={"active": True, "time": "09:00", "audio": "m.mp3"},
               schedule=_all_days(time="06:00"))
    nr = next_ring(cfg, WED_0500)
    assert nr["once"] is True  # one-time wins even though schedule has 06:00


# ----------------------------------------------------------------- due_now

def test_due_now_schedule_match():
    cfg = _cfg(schedule=_all_days(time="06:00"))
    assert due_now(cfg, datetime(2026, 6, 3, 6, 0))["once"] is False
    assert due_now(cfg, datetime(2026, 6, 3, 6, 1)) is None


def test_due_now_one_time_match_and_suppression():
    cfg = _cfg(one_time={"active": True, "time": "06:00", "audio": "m.mp3"},
               schedule=_all_days(time="06:30"))
    # one-time fires at its minute
    assert due_now(cfg, datetime(2026, 6, 3, 6, 0))["once"] is True
    # while one-time pending, the schedule's 06:30 is suppressed
    assert due_now(cfg, datetime(2026, 6, 3, 6, 30)) is None


# ------------------------------------------------------------ save_settings

def test_save_settings_roundtrip_preserves_keys(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "CONFIG_PATH", target)

    data = {"audio": {"volume": 50}, "alarm": {"one_time_alarm": {"active": True}}}
    assert settings_store.save_settings(data) is True
    assert target.exists()

    reloaded = json.loads(target.read_text())
    assert reloaded == data

    # mutate + re-save (the one-time deactivation path)
    data["alarm"]["one_time_alarm"]["active"] = False
    assert settings_store.save_settings(data) is True
    assert json.loads(target.read_text())["alarm"]["one_time_alarm"]["active"] is False
    assert json.loads(target.read_text())["audio"]["volume"] == 50  # untouched key kept


# --------------------------------------------------------- AlarmManager ring

class _FakeAO:
    def __init__(self):
        self.played = False
        self.stopped = False
        self.volume = 100
        self.ramp = "UNSET"

    def play_async(self, samples=None, sample_rate=None, loop=False, ramp=None):
        self.played = True
        self.ramp = ramp

    def interrupt(self):
        self.stopped = True


class _FakeAvatar:
    def __init__(self):
        self.touch_event = threading.Event()
        self.mode = None
        self.closed = False
        self.active_alarm = None
        self.active_alarm_history = []  # every set_active_alarm() value, in order

    def set_mode(self, m):
        self.mode = m

    def close_settings(self):
        self.closed = True

    def set_active_alarm(self, info):
        self.active_alarm = info
        self.active_alarm_history.append(info)


class _FakeDodi:
    def __init__(self, ao, volume=100):
        self._ao = ao
        self._audio_input = None
        self.volume = volume  # global audio volume (fallback for alarm.volume)

    def _init_audio_output(self):
        return self._ao


def test_alarm_ring_one_time_deactivates_and_saves():
    saves = []
    settings = {"alarm": {
        "stop_alarm_touch": True,
        "one_time_alarm": {"active": True, "time": "06:00", "audio": None},
        "schedule": {},
    }}
    av = _FakeAvatar()
    mgr = AlarmManager(settings, av, _FakeDodi(_FakeAO()), lambda s: saves.append(s))

    # Dismiss shortly after the (synchronous) ring begins.
    threading.Thread(target=lambda: (time.sleep(0.15), av.touch_event.set()), daemon=True).start()
    mgr._ring({"time": "06:00", "audio": None, "once": True, "day": None})

    assert settings["alarm"]["one_time_alarm"]["active"] is False  # deactivated
    assert len(saves) == 1                                          # persisted
    assert mgr.is_ringing() is False                               # back to idle
    assert av.closed is True                                       # settings force-closed


def test_alarm_ring_pins_active_alarm_then_clears():
    settings = {"alarm": {
        "stop_alarm_touch": True,
        "one_time_alarm": {"active": True, "time": "06:00", "audio": None},
        "schedule": {},
    }}
    av = _FakeAvatar()
    mgr = AlarmManager(settings, av, _FakeDodi(_FakeAO()), lambda s: None)

    threading.Thread(target=lambda: (time.sleep(0.15), av.touch_event.set()), daemon=True).start()
    mgr._ring({"time": "06:00", "audio": None, "once": True, "day": None})

    # While ringing the clock is pinned to this alarm, labelled "Today" (so it
    # never flips to "Tomorrow" once the minute passes); cleared on dismissal.
    assert {"time": "06:00", "label": "Today", "once": True, "day": None} \
        in av.active_alarm_history
    assert av.active_alarm is None


# ------------------------------------------------ alarm volume + ascending ramp

def _run_ring(monkeypatch, alarm_cfg, dodi_volume=100):
    """Ring once with a stubbed audio loader; return the _FakeAO it played on."""
    monkeypatch.setattr("alarm.manager.load_audio",
                        lambda p: (np.zeros(100, dtype=np.int16), 44100))
    av = _FakeAvatar()
    ao = _FakeAO()
    mgr = AlarmManager({"alarm": alarm_cfg}, av,
                       _FakeDodi(ao, volume=dodi_volume), lambda s: None)
    # Dismiss shortly after the synchronous ring begins.
    threading.Thread(target=lambda: (time.sleep(0.1), av.touch_event.set()),
                     daemon=True).start()
    mgr._ring({"time": "06:00", "audio": "x.mp3", "once": False, "day": "Wed"})
    return ao


def test_ring_sets_alarm_volume_and_ramp(monkeypatch):
    ao = _run_ring(monkeypatch, {
        "volume": 50,
        "ascending_volume": {"active": True, "start_volume": 10, "duration": 10},
        "one_time_alarm": {"active": False}, "schedule": {},
    })
    assert ao.volume == 50           # separate alarm volume, not global
    assert ao.ramp == (10, 10.0)     # (start_volume, duration)


def test_ring_no_ramp_when_inactive(monkeypatch):
    ao = _run_ring(monkeypatch, {
        "volume": 50,
        "ascending_volume": {"active": False, "start_volume": 10, "duration": 10},
        "one_time_alarm": {"active": False}, "schedule": {},
    })
    assert ao.volume == 50
    assert ao.ramp is None


def test_ring_no_ramp_when_malformed(monkeypatch):
    ao = _run_ring(monkeypatch, {
        "volume": 50,
        "ascending_volume": "not-a-dict",
        "one_time_alarm": {"active": False}, "schedule": {},
    })
    assert ao.ramp is None


def test_ring_no_ramp_when_start_ge_volume(monkeypatch):
    ao = _run_ring(monkeypatch, {
        "volume": 30,
        "ascending_volume": {"active": True, "start_volume": 40, "duration": 10},
        "one_time_alarm": {"active": False}, "schedule": {},
    })
    assert ao.volume == 30
    assert ao.ramp is None  # start >= ceiling -> no ascent


def test_ring_volume_falls_back_to_global(monkeypatch):
    ao = _run_ring(monkeypatch, {
        "ascending_volume": {"active": False},
        "one_time_alarm": {"active": False}, "schedule": {},
    }, dodi_volume=7)
    assert ao.volume == 7  # alarm.volume absent -> global audio volume
