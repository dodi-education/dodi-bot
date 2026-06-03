"""Tests for the alarm settings screen interaction logic + two-finger swipe.

Runs pygame headless (dummy SDL driver) so events/surfaces work without a display.
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

from display.settings_screen import SettingsScreen, _sound_label  # noqa: E402
from display.avatar import AvatarDisplay, DisplayMode  # noqa: E402


def _fresh_cfg():
    return {"alarm": {
        "stop_alarm_touch": True,
        "one_time_alarm": {"active": False, "time": "07:00", "audio": "morning.mp3"},
        "schedule": {d: {"active": d not in ("Sat", "Sun"),
                         "time": "06:30", "audio": "morning.mp3"}
                     for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
    }}


class FakeAvatar:
    def __init__(self, cfg):
        self._settings = cfg
        self.saves = 0
        self.closed = False

    def _save_settings(self, s):
        self.saves += 1
        return True

    def _alarm_cfg(self):
        return self._settings["alarm"]

    def close_settings(self):
        self.closed = True

    def open_settings(self):
        pass


def _screen():
    cfg = _fresh_cfg()
    av = FakeAvatar(cfg)
    scr = SettingsScreen(av)
    scr.open()
    surf = pygame.Surface((800, 800))
    scr.draw(surf)  # populate hit rects
    return scr, av, surf


def _tap(scr, rect):
    ev = pygame.event.Event(pygame.FINGERDOWN, finger_id=1,
                            x=rect.centerx / 800, y=rect.centery / 800, touch_id=0)
    scr.handle_event(ev)


# --------------------------------------------------------------- pure helpers

def test_sound_label():
    assert _sound_label("morning.mp3") == "Morning"
    assert _sound_label("birdsong.mp3") == "Birdsong"


def test_dial_angle_mapping():
    scr, _, _ = _screen()
    scr.overlay = "dial"
    scr._dfield = "h"
    scr._dial_from_xy((400, 472 - 224))   # straight up -> hour 0
    assert scr._dh == 0
    scr._dial_from_xy((400 + 224, 472))   # right -> hour 6 (quarter turn)
    assert scr._dh == 6
    scr._dial_from_xy((400, 472 + 224))   # down -> hour 12
    assert scr._dh == 12
    scr._dfield = "m"
    scr._dial_from_xy((400 + 224, 472))   # right -> minute 15
    assert scr._dm == 15


# ----------------------------------------------------------------- tapping

def test_tap_switches_to_once_tab():
    scr, _, surf = _screen()
    _tap(scr, scr._rects["tab_once"])
    assert scr.tab == "once"


def test_tap_day_selects_it():
    scr, _, surf = _screen()
    _tap(scr, scr._rects["chip_Fri"])
    assert scr.selected == "Fri"


def test_tap_active_toggles_and_saves():
    scr, av, surf = _screen()
    before = scr._entity().get("active")
    _tap(scr, scr._rects["active"])
    assert scr._entity().get("active") == (not before)
    assert av.saves == 1


def test_tap_close_closes_overlay():
    scr, av, surf = _screen()
    _tap(scr, scr._rects["close"])
    assert av.closed is True


def test_hero_opens_dial_then_set_commits_and_activates():
    scr, av, surf = _screen()
    scr.selected = "Sat"               # Sat starts inactive
    surf2 = pygame.Surface((800, 800))
    scr.draw(surf2)
    _tap(scr, scr._rects["hero"])
    assert scr.overlay == "dial"
    # hero loaded Sat's existing 06:30 into the dial; drag the hour to 12.
    scr._dfield = "h"
    scr._dial_from_xy((400, 472 + 224))  # hour 12
    scr.draw(surf2)                      # refresh dial rects
    _tap(scr, scr._rects["dial_set"])    # Set
    assert scr.overlay is None
    sat = scr._alarm["schedule"]["Sat"]
    assert sat["time"] == "12:30"        # hour dragged, minutes unchanged
    assert sat["active"] is True         # Set auto-activates
    assert av.saves >= 1


# ------------------------------------------------------- two-finger swipe

def test_two_finger_swipe_opens_settings():
    av = AvatarDisplay()
    # two fingers down near the top
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.2, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.2, touch_id=0), DisplayMode.AVATAR)
    assert av.settings_active is False
    # both drag well past the threshold
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.5, dx=0.0, dy=0.3, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.5, dx=0.0, dy=0.3, touch_id=0), DisplayMode.AVATAR)
    assert av.settings_active is True


def test_two_finger_swipe_works_in_clock_mode():
    # Night-mode clock should also accept the swipe-to-open gesture.
    av = AvatarDisplay()
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.2, touch_id=0), DisplayMode.CLOCK)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.2, touch_id=0), DisplayMode.CLOCK)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.6, dx=0.0, dy=0.4, touch_id=0), DisplayMode.CLOCK)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.6, dx=0.0, dy=0.4, touch_id=0), DisplayMode.CLOCK)
    assert av.settings_active is True


def test_swipe_ignored_in_blank_mode():
    # Screen-off (deactivated display) ignores the gesture.
    av = AvatarDisplay()
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.2, touch_id=0), DisplayMode.BLANK)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.2, touch_id=0), DisplayMode.BLANK)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.6, dx=0.0, dy=0.4, touch_id=0), DisplayMode.BLANK)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.6, dx=0.0, dy=0.4, touch_id=0), DisplayMode.BLANK)
    assert av.settings_active is False
