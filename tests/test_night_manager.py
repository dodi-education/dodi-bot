"""Runtime enable/disable of the night-mode manager (NightModeManager.set_enabled).

Uses an empty schedule so the worker never enters a session (no display/sysfs
side effects) — we only assert the scheduler thread's lifecycle.
"""

import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from night_mode.manager import NightModeManager  # noqa: E402


def _mgr(enabled=False):
    return NightModeManager({"enabled": enabled, "schedule": {}}, avatar=None, dodi=None)


def test_start_is_noop_while_disabled():
    mgr = _mgr(enabled=False)
    mgr.start()
    assert mgr._thread is None


def test_set_enabled_starts_then_stops_thread():
    mgr = _mgr(enabled=False)
    assert mgr._thread is None

    mgr.set_enabled(True)
    assert mgr._enabled is True
    assert mgr._thread is not None and mgr._thread.is_alive()

    mgr.set_enabled(False)
    assert mgr._enabled is False
    assert mgr._thread is None          # join completed synchronously
    time.sleep(0.05)


def test_set_enabled_is_idempotent():
    mgr = _mgr(enabled=False)
    mgr.set_enabled(False)              # already off -> no-op
    assert mgr._thread is None

    mgr.set_enabled(True)
    t = mgr._thread
    mgr.set_enabled(True)              # already on -> same thread, no duplicate
    assert mgr._thread is t

    mgr.set_enabled(False)
    assert mgr._thread is None


def test_enabled_at_boot_starts_on_start():
    mgr = _mgr(enabled=True)
    mgr.start()
    assert mgr._thread is not None and mgr._thread.is_alive()
    mgr.set_enabled(False)
    assert mgr._thread is None


# ---------------------------------------- display deference to a ringing alarm

class _FakeAvatar:
    def __init__(self):
        self.mode = "UNSET"

    def set_mode(self, m):
        self.mode = m


class _FakeAlarm:
    def __init__(self, ringing):
        self._ringing = ringing

    def is_ringing(self):
        return self._ringing


class _FakeDodi:
    def __init__(self, alarm):
        self._alarm = alarm


def _restore_mgr(monkeypatch, ringing):
    """A manager wired to a fake alarm, with sysfs backlight calls stubbed out
    (these tests run on the real Pi — never touch the actual display)."""
    import night_mode.manager as nm
    monkeypatch.setattr(nm, "set_power", lambda *a, **k: None)
    monkeypatch.setattr(nm, "set_brightness", lambda *a, **k: None)
    av = _FakeAvatar()
    mgr = NightModeManager({"enabled": True, "schedule": {}}, avatar=av,
                           dodi=_FakeDodi(_FakeAlarm(ringing=ringing)))
    return mgr, av


def test_restore_display_defers_to_ringing_alarm(monkeypatch):
    # Night mode lifting under a ringing alarm must NOT grab the display back.
    mgr, av = _restore_mgr(monkeypatch, ringing=True)
    mgr._restore_display()
    assert av.mode == "UNSET"  # left the alarm's screen untouched


def test_restore_display_sets_avatar_when_no_alarm(monkeypatch):
    from display import DisplayMode
    mgr, av = _restore_mgr(monkeypatch, ringing=False)
    mgr._restore_display()
    assert av.mode == DisplayMode.AVATAR  # normal restore when nothing rings
