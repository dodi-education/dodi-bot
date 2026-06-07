"""Tests for the system settings screen (swipe-up overlay).

Runs pygame headless (dummy SDL driver). Grows phase by phase; for now it covers
section switching, close, and a "draw doesn't raise" smoke test over every section.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

pygame.init()
pygame.font.init()

from display.system_settings_screen import SystemSettingsScreen, SECTIONS  # noqa: E402


def _fresh_settings():
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "audio": {"volume": 10},
        "alarm": {"volume": 25, "ascending_volume": {"active": True, "duration": 20}},
        "weather": {"location_name": "Vienna", "country_code": "AT", "lat": 48.2, "lon": 16.4},
        "power_options": {"turn_off_display": 60, "turn_off_display_night_mode": 3},
        "night_mode": {
            "enabled": True,
            "display": {"mode": "time", "brightness": 10},
            "schedule": {d: [{"timespan": "22:00-24:00"}, {"timespan": "00:00-06:00"}] for d in days},
        },
    }


class FakeAvatar:
    def __init__(self, settings):
        self._settings = settings
        self.saves = 0
        self.applied = []
        self.closed = False

    def _save_settings(self, s):
        self.saves += 1
        return True

    def _system_apply(self, key, value):
        self.applied.append((key, value))

    def close_settings(self):
        self.closed = True


def _screen():
    av = FakeAvatar(_fresh_settings())
    scr = SystemSettingsScreen(av)
    # Keep tests hermetic: no real nmcli / network from the controllers.
    scr._wifi.start_scan = lambda *a, **k: None
    scr._geo.start_search = lambda *a, **k: None
    scr.open()
    surf = pygame.Surface((800, 800))
    scr.draw(surf)  # populate hit rects
    return scr, av, surf


def _tap(scr, rect):
    ev = pygame.event.Event(pygame.FINGERDOWN, finger_id=1,
                            x=rect.centerx / 800, y=rect.centery / 800, touch_id=0)
    scr.handle_event(ev)


def _set_slider(scr, key, frac):
    """Press + release the slider at the given fraction of its track."""
    x, w, vmin, vmax = scr._slider_geom[key]
    px = x + frac * w
    cy = scr._rects[f"slider_{key}"].centery
    for kind in (pygame.FINGERDOWN, pygame.FINGERUP):
        scr.handle_event(pygame.event.Event(kind, finger_id=1, x=px / 800, y=cy / 800, touch_id=0))


def test_open_resets_to_general():
    scr, _, _ = _screen()
    assert scr.section == "general"
    assert scr.sub is None


def test_tap_section_switches():
    scr, _, surf = _screen()
    for sid, _, _ in SECTIONS:
        _tap(scr, scr._rects[f"section_{sid}"])
        assert scr.section == sid


def test_tap_close_closes_overlay():
    scr, av, _ = _screen()
    _tap(scr, scr._rects["close"])
    assert av.closed is True


def test_draw_every_section_does_not_raise():
    scr, _, surf = _screen()
    for sid, _, _ in SECTIONS:
        scr.section = sid
        scr.draw(surf)  # must not raise (exercises icons + layout)


# ----------------------------------------------------- General controls

def test_master_volume_slider_sets_value_applies_and_saves():
    scr, av, surf = _screen()
    _set_slider(scr, "master_vol", 0.5)
    assert scr._get(("audio", "volume")) == 50
    assert ("audio.volume", 50) in av.applied   # live-applied during drag
    assert av.saves >= 1                          # persisted on release


def test_alarm_volume_slider_persist_only():
    scr, av, surf = _screen()
    _set_slider(scr, "alarm_vol", 0.25)
    assert scr._get(("alarm", "volume")) == 25
    assert all(k != "alarm.volume" for k, _ in av.applied)  # no live hook


def test_ascending_switch_toggles():
    scr, av, surf = _screen()
    before = bool(scr._get(("alarm", "ascending_volume", "active")))
    _tap(scr, scr._rects["switch_ascending"])
    assert bool(scr._get(("alarm", "ascending_volume", "active"))) == (not before)


# ------------------------------------------------------- Power controls

def test_power_switch_toggles_null_and_remembers_value():
    scr, av, surf = _screen()
    scr.section = "power"
    scr.draw(surf)
    assert scr._get(("power_options", "turn_off_display")) == 60
    _tap(scr, scr._rects["switch_off_day"])           # disable -> null
    assert scr._get(("power_options", "turn_off_display")) is None
    assert ("power_options.turn_off_display", None) in av.applied
    scr.draw(surf)
    _tap(scr, scr._rects["switch_off_day"])           # re-enable -> remembered 60
    assert scr._get(("power_options", "turn_off_display")) == 60


def test_power_stepper_cycles_presets():
    scr, av, surf = _screen()
    scr.section = "power"
    scr.draw(surf)
    _tap(scr, scr._rects["step_off_day_plus"])        # 60 -> 90
    assert scr._get(("power_options", "turn_off_display")) == 90
    scr.draw(surf)
    _tap(scr, scr._rects["step_off_day_minus"])       # 90 -> 60
    assert scr._get(("power_options", "turn_off_display")) == 60


# ------------------------------------------------------- Night controls

def test_night_brightness_slider_applies_with_min_1():
    scr, av, surf = _screen()
    scr.section = "night"
    scr.draw(surf)
    _set_slider(scr, "night_bri", 0.0)
    assert scr._get(("night_mode", "display", "brightness")) == 1   # min is 1
    assert ("night_mode.display.brightness", 1) in av.applied


def test_night_mode_switch_toggles_and_applies_live():
    scr, av, surf = _screen()
    scr.section = "night"
    scr.draw(surf)
    before = bool(scr._get(("night_mode", "enabled")))
    _tap(scr, scr._rects["switch_night_enabled"])
    assert bool(scr._get(("night_mode", "enabled"))) == (not before)
    assert ("night_mode.enabled", not before) in av.applied   # live start/stop hook


# --------------------------------------------------- Schedule sub-sheet

def _open_schedule(scr, surf):
    scr.section = "night"
    scr.draw(surf)
    _tap(scr, scr._rects["nav_schedule"])
    scr.draw(surf)


def test_schedule_opens_from_night():
    scr, av, surf = _screen()
    _open_schedule(scr, surf)
    assert scr.sub == "schedule"


def test_schedule_edit_round_trips_timespan():
    scr, av, surf = _screen()
    _open_schedule(scr, surf)               # Mon stored order: [22:00-24:00, 00:00-06:00]
    _tap(scr, scr._rects["sched_chip_0"])   # the 22:00-24:00 span
    assert (scr._sched_edit["fh"], scr._sched_edit["fm"]) == (22, 0)
    scr.draw(surf)
    _tap(scr, scr._rects["sched_from_m_up"])  # minute 0 -> 15
    assert scr._sched_edit["fm"] == 15
    scr.draw(surf)
    _tap(scr, scr._rects["sched_save"])
    mon = scr._get(("night_mode", "schedule"))["Mon"]
    assert any(s["timespan"] == "22:15-24:00" for s in mon)
    assert any(k == "night_mode.schedule" for k, _ in av.applied)


def test_schedule_add_and_delete_period():
    scr, av, surf = _screen()
    _open_schedule(scr, surf)
    n0 = len(scr._get(("night_mode", "schedule"))["Mon"])
    _tap(scr, scr._rects["sched_add"])
    assert scr._sched_edit["idx"] == "new"
    scr.draw(surf)
    _tap(scr, scr._rects["sched_save"])     # default 22:00-23:00 added
    assert len(scr._get(("night_mode", "schedule"))["Mon"]) == n0 + 1
    scr.draw(surf)
    _tap(scr, scr._rects["sched_del_0"])    # delete first
    assert len(scr._get(("night_mode", "schedule"))["Mon"]) == n0


def test_schedule_draw_editor_does_not_raise():
    scr, av, surf = _screen()
    _open_schedule(scr, surf)
    _tap(scr, scr._rects["sched_chip_0"])
    scr.draw(surf)  # editor view must render


# ------------------------------------------------------- Wi-Fi sheet

def _kb_tap(scr, ch):
    for rect, c in scr._rects["_kb_keys"]:
        if c == ch:
            _tap(scr, rect)
            return
    raise AssertionError(f"no keyboard key {ch!r}")


def _open_wifi(scr, surf, nets):
    from network.wifi import Network  # noqa: F401
    scr.sub = "wifi"
    scr._wifi._networks = nets
    scr._wifi._scan_state = "done"
    scr.draw(surf)


def test_wifi_open_network_taps_to_connect():
    from network.wifi import Network
    scr, av, surf = _screen()
    _open_wifi(scr, surf, [Network("OpenNet", 70, secure=False),
                           Network("SecureNet", 80, secure=True)])
    calls = []
    scr._wifi.start_connect = lambda s, p: calls.append((s, p))
    _tap(scr, scr._rects["_wifi_rows"][0][0])     # OpenNet row
    assert scr._wifi_target == "OpenNet"
    assert calls == [("OpenNet", None)]


def test_wifi_secure_network_prompts_password_then_connects():
    from network.wifi import Network
    scr, av, surf = _screen()
    _open_wifi(scr, surf, [Network("SecureNet", 80, secure=True)])
    _tap(scr, scr._rects["_wifi_rows"][0][0])
    assert scr._wifi_target == "SecureNet"
    scr.draw(surf)                                 # keyboard now shown
    calls = []
    scr._wifi.start_connect = lambda s, p: calls.append((s, p))
    _kb_tap(scr, "a")
    _kb_tap(scr, "b")
    assert scr._wifi_pw == "ab"
    _tap(scr, scr._rects["kb_action"])             # Connect
    assert calls == [("SecureNet", "ab")]


def test_wifi_keyboard_shift_and_backspace():
    from network.wifi import Network
    scr, av, surf = _screen()
    _open_wifi(scr, surf, [Network("S", 80, secure=True)])
    _tap(scr, scr._rects["_wifi_rows"][0][0])
    scr.draw(surf)
    _tap(scr, scr._rects["kb_shift"])
    assert scr._kb_shift is True
    scr.draw(surf)
    _kb_tap(scr, "A")                              # shifted letter
    assert scr._wifi_pw == "A"
    assert scr._kb_shift is False                  # auto-reset after a key
    scr.draw(surf)
    _tap(scr, scr._rects["kb_back"])
    assert scr._wifi_pw == ""


def test_wifi_back_returns_to_list():
    from network.wifi import Network
    scr, av, surf = _screen()
    _open_wifi(scr, surf, [Network("S", 80, secure=True)])
    _tap(scr, scr._rects["_wifi_rows"][0][0])
    scr.draw(surf)
    _tap(scr, scr._rects["sheet_back"])
    assert scr._wifi_target is None


def test_wifi_connected_network_closes_sheet():
    from network.wifi import Network
    scr, av, surf = _screen()
    _open_wifi(scr, surf, [Network("Home", 80, secure=True, in_use=True)])
    _tap(scr, scr._rects["_wifi_rows"][0][0])
    assert scr.sub is None


# --------------------------------------------------- Location sheet

def test_location_pick_writes_weather():
    from network.geocode import Place
    scr, av, surf = _screen()
    scr.sub = "location"
    scr._geo._results = [Place("Berlin", "DE", 52.52, 13.40, "Berlin")]
    scr._geo._state = "done"
    scr._geo.start_search = lambda q: None
    scr.draw(surf)
    _tap(scr, scr._rects["_loc_rows"][0][0])
    assert scr._get(("weather", "location_name")) == "Berlin"
    assert scr._get(("weather", "country_code")) == "DE"
    assert scr._get(("weather", "lat")) == 52.52
    assert scr._get(("weather", "lon")) == 13.40
    assert scr.sub is None


def test_location_keyboard_types_and_searches():
    scr, av, surf = _screen()
    scr.sub = "location"
    scr._geo._results = []
    scr._geo._state = "idle"
    queries = []
    scr._geo.start_search = lambda q: queries.append(q)
    scr.draw(surf)
    _kb_tap(scr, "b")
    _kb_tap(scr, "e")
    assert scr._loc_query == "be"
    assert queries[-1] == "be"


def test_wifi_and_location_sheets_draw_does_not_raise():
    scr, av, surf = _screen()
    for sub in ("wifi", "location"):
        scr.sub = sub
        scr.draw(surf)
