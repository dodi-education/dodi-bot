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
    scr._dial_from_xy((400, 416 - 224))   # straight up -> hour 0
    assert scr._dh == 0
    scr._dial_from_xy((400 + 224, 416))   # right -> hour 6 (quarter turn)
    assert scr._dh == 6
    scr._dial_from_xy((400, 416 + 224))   # down -> hour 12
    assert scr._dh == 12
    scr._dfield = "m"
    scr._dial_from_xy((400 + 224, 416))   # right -> minute 15
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
    scr._dial_from_xy((400, 416 + 224))  # hour 12
    scr.draw(surf2)                      # refresh dial rects
    _tap(scr, scr._rects["dial_set"])    # Set
    assert scr.overlay is None
    sat = scr._alarm["schedule"]["Sat"]
    assert sat["time"] == "12:30"        # hour dragged, minutes unchanged
    assert sat["active"] is True         # Set auto-activates
    assert av.saves >= 1


def test_dial_cancel_closes_without_committing():
    scr, av, surf = _screen()
    _tap(scr, scr._rects["hero"])
    assert scr.overlay == "dial"
    surf2 = pygame.Surface((800, 800))
    scr.draw(surf2)                      # populate the (bottom) cancel rect
    cancel = scr._rects["dial_cancel"]
    assert cancel.centery > 600          # cancel button lives at the bottom now
    _tap(scr, cancel)
    assert scr.overlay is None


def test_scrim_blurs_and_dims():
    from display import widgets
    surf = pygame.Surface((800, 800))
    surf.fill((255, 255, 255))
    pygame.draw.rect(surf, (0, 0, 0), pygame.Rect(0, 0, 400, 800))  # hard edge
    widgets.scrim(surf)
    # Dim: a white region is darkened by the 50% navy overlay.
    r, g, b = surf.get_at((600, 400))[:3]
    assert r < 240 and b < 250
    # Blur: the hard black/white edge at x=400 is no longer a clean split.
    edge = surf.get_at((400, 400))[:3]
    assert 30 < edge[0] < 230


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


def test_two_finger_swipe_up_opens_system_settings():
    av = AvatarDisplay()
    # two fingers down near the bottom
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.8, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.8, touch_id=0), DisplayMode.AVATAR)
    assert av.settings_active is False
    # both drag up well past the threshold
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.5, dx=0.0, dy=-0.3, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.5, dx=0.0, dy=-0.3, touch_id=0), DisplayMode.AVATAR)
    assert av.settings_active is True
    assert av.active_overlay == "system"


def test_swipe_down_opens_alarm_overlay():
    av = AvatarDisplay()
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.2, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.2, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.5, dx=0.0, dy=0.3, touch_id=0), DisplayMode.AVATAR)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.5, dx=0.0, dy=0.3, touch_id=0), DisplayMode.AVATAR)
    assert av.active_overlay == "alarm"


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


# ------------------------------------------- single-finger horizontal swipe

def _hswipe(av, mode, x_from, x_to, y=0.5):
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=x_from, y=y, touch_id=0), mode, 100.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=x_to, y=y, touch_id=0), mode, 100.0)


def test_left_swipe_enters_info_from_avatar():
    av = AvatarDisplay()
    _hswipe(av, DisplayMode.AVATAR, 0.85, 0.40)
    assert av.mode == DisplayMode.INFO


def test_right_swipe_returns_to_avatar_from_info():
    av = AvatarDisplay()
    av.set_mode(DisplayMode.INFO)
    _hswipe(av, DisplayMode.INFO, 0.20, 0.70)
    assert av.mode == DisplayMode.AVATAR


def test_left_swipe_does_nothing_from_info():
    # No wraparound: left only commits from AVATAR.
    av = AvatarDisplay()
    av.set_mode(DisplayMode.INFO)
    _hswipe(av, DisplayMode.INFO, 0.85, 0.40)
    assert av.mode == DisplayMode.INFO


def test_vertical_drag_is_not_a_horizontal_swipe():
    av = AvatarDisplay()
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.5, y=0.2, touch_id=0), DisplayMode.AVATAR, 100.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.55, y=0.85, touch_id=0), DisplayMode.AVATAR, 100.0)
    assert av.mode == DisplayMode.AVATAR


def test_small_move_marks_gesture_moved_but_no_switch():
    # A tap-sized wobble neither switches mode nor stays "unmoved" past TAP_RADIUS.
    av = AvatarDisplay()
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.5, y=0.5, touch_id=0), DisplayMode.AVATAR, 100.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.53, y=0.5, touch_id=0), DisplayMode.AVATAR, 100.0)
    assert av.mode == DisplayMode.AVATAR
    assert av._h_gesture is not None and av._h_gesture["fired"] is False


def test_two_finger_swipe_does_not_trigger_horizontal_swipe():
    # Regression: a second finger's motion vs. the first finger's origin has a
    # large horizontal delta early in the gesture; the single-finger tracker must
    # cancel on the 2nd finger so it never switches to INFO.
    av = AvatarDisplay()
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.30, y=0.20, touch_id=0), DisplayMode.AVATAR, 100.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.70, y=0.20, touch_id=0), DisplayMode.AVATAR, 100.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.30, y=0.25, touch_id=0), DisplayMode.AVATAR, 100.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.70, y=0.25, touch_id=0), DisplayMode.AVATAR, 100.0)
    assert av.mode == DisplayMode.AVATAR
    assert av._h_gesture is not None and av._h_gesture["cancel"] is True


def test_reset_clears_stale_fingers_so_single_swipe_works_again():
    # Regression: a two-finger swipe leaves touch ids in _h_fingers whose UPs are
    # swallowed by the opened overlay (ids are never reused on this panel), so
    # without a reset every later single-finger swipe trips the multi-touch guard.
    av = AvatarDisplay()
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1019, x=0.4, y=0.2, touch_id=0), DisplayMode.AVATAR, 1.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1020, x=0.6, y=0.2, touch_id=0), DisplayMode.AVATAR, 1.0)
    assert len(av._h_fingers) == 2  # both ids stuck (their UPs would be swallowed)

    av._reset_h_gesture()           # what the loop does while a settings overlay is open
    assert av._h_fingers == set()
    assert av._h_gesture is None

    # A fresh single-finger left swipe now works again.
    av._track_h_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1025, x=0.85, y=0.5, touch_id=0), DisplayMode.AVATAR, 2.0)
    av._track_h_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1025, x=0.40, y=0.5, touch_id=0), DisplayMode.AVATAR, 2.0)
    assert av.mode == DisplayMode.INFO


def test_track_swipe_clears_stale_finger_in_info_mode():
    # A finger that entered _swipe_fingers in AVATAR but lifts in INFO must be
    # cleared, or its frozen ~0 delta blocks all later two-finger detection.
    av = AvatarDisplay()
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1238, x=0.5, y=0.5, touch_id=0), DisplayMode.AVATAR)
    assert 1238 in av._swipe_fingers
    av._track_swipe(pygame.event.Event(pygame.FINGERUP, finger_id=1238, x=0.5, y=0.5, touch_id=0), DisplayMode.INFO)
    assert av._swipe_fingers == {}


def test_left_swipe_then_two_finger_swipe_still_opens_settings():
    # Regression for the reported "two-finger stops working after a left/right
    # swipe": the left swipe's finger-up lands in INFO; without clearing it leaks
    # into _swipe_fingers and poisons the next two-finger swipe.
    av = AvatarDisplay()

    def route(ev):  # mimic the event loop (both trackers, every mode)
        cm = av.mode
        av._track_h_swipe(ev, cm, 1.0)
        av._track_swipe(ev, cm)

    # single-finger LEFT: down+motion in AVATAR (-> INFO), up in INFO
    route(pygame.event.Event(pygame.FINGERDOWN, finger_id=1238, x=0.85, y=0.5, touch_id=0))
    route(pygame.event.Event(pygame.FINGERMOTION, finger_id=1238, x=0.40, y=0.5, touch_id=0))
    assert av.mode == DisplayMode.INFO
    route(pygame.event.Event(pygame.FINGERUP, finger_id=1238, x=0.40, y=0.5, touch_id=0))
    assert av._swipe_fingers == {}  # the stale finger was cleared in INFO

    # single-finger RIGHT back to AVATAR
    route(pygame.event.Event(pygame.FINGERDOWN, finger_id=1239, x=0.20, y=0.5, touch_id=0))
    route(pygame.event.Event(pygame.FINGERMOTION, finger_id=1239, x=0.70, y=0.5, touch_id=0))
    route(pygame.event.Event(pygame.FINGERUP, finger_id=1239, x=0.70, y=0.5, touch_id=0))
    assert av.mode == DisplayMode.AVATAR

    # a two-finger swipe-down must STILL open the alarm overlay
    for ev in [
        pygame.event.Event(pygame.FINGERDOWN, finger_id=1240, x=0.4, y=0.20, touch_id=0),
        pygame.event.Event(pygame.FINGERDOWN, finger_id=1241, x=0.6, y=0.20, touch_id=0),
        pygame.event.Event(pygame.FINGERMOTION, finger_id=1240, x=0.4, y=0.55, touch_id=0),
        pygame.event.Event(pygame.FINGERMOTION, finger_id=1241, x=0.6, y=0.55, touch_id=0),
    ]:
        route(ev)
    assert av.active_overlay == "alarm"


def test_two_finger_swipe_down_opens_alarm_from_info_screen():
    # Two-finger settings swipes must work on the info screen too, not just avatar.
    av = AvatarDisplay()
    av.set_mode(DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.2, touch_id=0), DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.2, touch_id=0), DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.55, dx=0.0, dy=0.35, touch_id=0), DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.55, dx=0.0, dy=0.35, touch_id=0), DisplayMode.INFO)
    assert av.active_overlay == "alarm"


def test_two_finger_swipe_up_opens_system_from_info_screen():
    av = AvatarDisplay()
    av.set_mode(DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.8, touch_id=0), DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.8, touch_id=0), DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.45, dx=0.0, dy=-0.35, touch_id=0), DisplayMode.INFO)
    av._track_swipe(pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.45, dx=0.0, dy=-0.35, touch_id=0), DisplayMode.INFO)
    assert av.active_overlay == "system"


def test_two_finger_swipe_down_still_opens_alarm_with_h_tracker():
    # Full pipeline: horizontal tracker first (as the event loop runs it), then
    # the two-finger tracker. Settings must still open and mode stays AVATAR.
    av = AvatarDisplay()
    events = [
        pygame.event.Event(pygame.FINGERDOWN, finger_id=1, x=0.4, y=0.2, touch_id=0),
        pygame.event.Event(pygame.FINGERDOWN, finger_id=2, x=0.6, y=0.2, touch_id=0),
        pygame.event.Event(pygame.FINGERMOTION, finger_id=1, x=0.4, y=0.55, dx=0.0, dy=0.35, touch_id=0),
        pygame.event.Event(pygame.FINGERMOTION, finger_id=2, x=0.6, y=0.55, dx=0.0, dy=0.35, touch_id=0),
    ]
    for ev in events:
        av._track_h_swipe(ev, DisplayMode.AVATAR, 100.0)
        av._track_swipe(ev, DisplayMode.AVATAR)
    assert av.mode == DisplayMode.AVATAR
    assert av.active_overlay == "alarm"
