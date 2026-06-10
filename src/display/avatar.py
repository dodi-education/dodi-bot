"""
DODI Avatar Display - Borderless Blue Bird Face with Bigger Eyes and Beak.

Renders only the inner blue face of the bird avatar onto the round 800x800 screen.
The eyes have been enlarged, and the nose has been redesigned to look like a
more prominent beak.
"""

import datetime as _dt
import logging
import math
import os
import random
import threading
import time
from enum import Enum
from pathlib import Path

import pygame

from . import theme
from .settings_screen import SettingsScreen
from .system_settings_screen import SystemSettingsScreen
from .info_screen import InfoScreen
from sensors.cpu_temp import CpuTempMonitor
from alarm.schedule import next_ring

logger = logging.getLogger("DODI.display")


class DisplayMode(Enum):
    AVATAR = "avatar"
    INFO = "info"
    CLOCK = "clock"
    BLANK = "blank"

# Two taps within this many seconds count as a double-tap (mode toggle).
DOUBLE_TAP_WINDOW = 0.30

# Two fingers dragging by this much (normalized) opens a settings overlay:
# down -> alarm settings, up -> system settings.
SWIPE_DOWN_THRESHOLD = 0.18
SWIPE_UP_THRESHOLD = 0.18

# Single-finger horizontal swipe (normalized 0..1): left opens the info screen
# (from the avatar), right returns to the avatar (from the info screen). A tap is
# anything that never moves past TAP_RADIUS, so a swipe never also fires a laugh.
H_SWIPE_THRESHOLD = 0.22
H_SWIPE_MAX_OFF_AXIS = 0.16
TAP_RADIUS = 0.045

# Render-loop pacing for the adaptive_fps path. We pump input at IDLE_FPS so
# touch stays responsive, but only repaint when the visible content actually
# changes; while something is moving (blink/laugh, or the settings overlay) we
# step up to ACTIVE_FPS. PAUSED_FPS is the bare input-pump rate while rendering
# is parked (screen off) — no drawing happens at all.
ACTIVE_FPS = 30
IDLE_FPS = 10
PAUSED_FPS = 10

# Screen dimensions (Waveshare 3.4-inch round DSI display)
SCREEN_SIZE = 800
CX = SCREEN_SIZE // 2   # 400
CY = SCREEN_SIZE // 2   # 400


def _classify_tap(last_ts: float, now: float, window: float) -> tuple[str, float]:
    """Classify a screen tap as ``"single"`` or ``"double"``.

    Returns ``(kind, new_last_ts)``. A tap within ``window`` seconds of the
    previous one is a ``"double"`` and resets the timer to ``0.0`` (so a rapid
    triple-tap does not register two doubles). Otherwise it is a ``"single"``
    and its timestamp is recorded. A ``last_ts`` of ``0.0`` means "no previous
    tap" (``time.monotonic()`` is never 0 in practice).
    """
    if last_ts and now - last_ts <= window:
        return "double", 0.0
    return "single", now

# --- Color palette ---
# Background is now the face fill color
COL_FACE_FILL = (150, 210, 240)       # Light blue face
COL_BG = COL_FACE_FILL                # Background matches face

COL_FACE_SHADOW = (120, 190, 230)     # Slightly darker blue for shadows/cheeks/mouth
COL_EYE_DARK = (20, 30, 50)           # Dark navy/black eyes
COL_EYE_IRIS = (30, 70, 120)          # Dark blue iris
COL_EYE_HIGHLIGHT = (255, 255, 255)   # White sparkles
COL_BEAK_FILL = (80, 120, 160)        # Medium blue beak
COL_BEAK_HIGHLIGHT = (130, 170, 210)  # Lighter blue beak highlight
# Headphones overlay (DEAF mode) — a pre-made PNG blitted over the normal face.
_HEADPHONES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "img" / "headphones.png"
)
HP_WIDTH = 800          # Scaled-to width of the overlay on the 800px screen.
HP_EARCUP_FRAC = 0.467  # Vertical fraction of the source image at ear-cup center.
HP_EARCUP_Y = CY - 20   # Screen y where the ear cups should sit (ear level).

# Layout constants
FACE_RADIUS = 360
# Outlines removed
OUTLINE_THICKNESS = 0

# --- MODIFIED: Eye positions and size ---
# Moved up and made significantly larger
EYE_Y = CY - 10
EYE_X_OFFSET = 150
EYE_WIDTH = 140
EYE_HEIGHT = 160

# --- MODIFIED: Beak position and size ---
# Moved up slightly to accommodate larger eyes
BEAK_Y = CY + 100
# Beak dimensions are now handled dynamically in the draw function for a better shape

class AvatarDisplay:
    """
    Animated avatar display for DODI's round screen.
    Runs pygame rendering in a dedicated thread.
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._running = False
        self._laugh_event = threading.Event()
        self._stop_event = threading.Event()
        self._touch_event = threading.Event()
        self._double_tap_event = threading.Event()
        # Set on every screen tap in any mode; the control loop polls/clears it
        # to reset the idle-display timer and to wake a parked screen.
        self._interaction_event = threading.Event()
        # When True the render thread stops drawing/flipping (keeps pumping
        # input); used by the idle display-off feature. Plain bool, read in the
        # loop like _running.
        self._render_paused = False
        self._last_tap_ts = 0.0
        self._swipe_fingers: dict = {}  # finger_id -> [start_y, cur_y]
        # Single-finger horizontal swipe / deferred-tap state (None between touches).
        self._h_gesture: dict | None = None
        self._h_fingers: set = set()  # finger ids currently down (multi-touch guard)
        self._mode_lock = threading.Lock()
        self._mode = DisplayMode.AVATAR
        self._deaf = False
        self._active_overlay: str | None = None  # None | "alarm" | "system"
        # While an alarm is ringing the AlarmManager pins it here (an nr-shaped
        # dict) so the clock's next-alarm line keeps showing the current ring
        # instead of rolling forward to "Tomorrow" once the minute ticks over.
        self._active_alarm: dict | None = None
        self._clock_font: pygame.font.Font | None = None
        self._clock_sub_font: pygame.font.Font | None = None
        # Shared settings + persistence (bound by main(); used by the clock's
        # next-alarm line and the settings screen).
        self._settings: dict | None = None
        self._save_settings = None
        self._system_apply = None       # optional live-apply hook (bound by main())
        self._settings_screen = None
        self._system_settings_screen = None
        self._info_screen = None
        # Continuous CPU-temperature sampler (feeds the info screen's gauge).
        self._cpu_monitor: CpuTempMonitor | None = None
        # Optional shared weather controller (bound by main so the info screen and
        # the AI system prompt read one forecast cache); None -> info screen makes
        # its own.
        self._weather_controller = None

    @property
    def mode(self) -> DisplayMode:
        with self._mode_lock:
            return self._mode

    def set_mode(self, mode: DisplayMode) -> None:
        """Switch render mode. Safe to call from any thread."""
        with self._mode_lock:
            self._mode = mode
        # Drain any stale touch event when leaving night-mode-style modes.
        if mode == DisplayMode.AVATAR:
            self._touch_event.clear()

    @property
    def touch_event(self) -> threading.Event:
        """Event set when the user taps the screen in CLOCK/BLANK mode."""
        return self._touch_event

    @property
    def interaction_event(self) -> threading.Event:
        """Event set on any screen tap, regardless of mode.

        The control loop uses this to reset the idle-display timer and to wake a
        parked (rendering-off) screen. Independent of ``touch_event`` (alarm
        dismissal) and the AVATAR gesture events.
        """
        return self._interaction_event

    @property
    def double_tap_event(self) -> threading.Event:
        """Event set when the user double-taps the screen in AVATAR mode.

        The control loop polls and clears this to toggle DEAF <-> NORMAL.
        """
        return self._double_tap_event

    @property
    def deaf(self) -> bool:
        with self._mode_lock:
            return self._deaf

    def set_deaf(self, deaf: bool) -> None:
        """Show/hide the headphones (DEAF mode). Safe to call from any thread."""
        with self._mode_lock:
            self._deaf = deaf

    @property
    def active_alarm(self) -> dict | None:
        with self._mode_lock:
            return self._active_alarm

    def set_active_alarm(self, info: dict | None) -> None:
        """Pin the currently-ringing alarm (an nr-shaped dict) so the clock's
        next-alarm line keeps showing it for as long as it rings; pass ``None``
        when it stops. Safe to call from any thread."""
        with self._mode_lock:
            self._active_alarm = info

    @property
    def settings_active(self) -> bool:
        """True while any settings overlay (alarm or system) is open."""
        with self._mode_lock:
            return self._active_overlay is not None

    @property
    def active_overlay(self) -> str | None:
        """Which overlay is open: ``None`` | ``"alarm"`` | ``"system"`` (any thread)."""
        with self._mode_lock:
            return self._active_overlay

    def open_settings(self) -> None:
        """Open the alarm settings overlay (safe from any thread)."""
        with self._mode_lock:
            self._active_overlay = "alarm"

    def open_system_settings(self) -> None:
        """Open the system settings overlay (safe from any thread)."""
        with self._mode_lock:
            self._active_overlay = "system"

    def close_settings(self) -> None:
        """Close whichever settings overlay is open (safe from any thread)."""
        with self._mode_lock:
            self._active_overlay = None

    def bind_settings(self, settings: dict, save_cb, apply_cb=None) -> None:
        """Give the display access to the live settings dict + callbacks.

        ``save_cb(settings)`` persists; the optional ``apply_cb(key, value)`` lets the
        system settings screen push changes that must take effect immediately (volume,
        display-power timers, night brightness/schedule). Used by the clock's
        next-alarm line and both settings screens.
        """
        self._settings = settings
        self._save_settings = save_cb
        self._system_apply = apply_cb

    def bind_weather(self, controller) -> None:
        """Share a weather controller so the info screen reuses one forecast cache.

        Must be called before :meth:`start` (the info screen reads it on creation).
        """
        self._weather_controller = controller

    def _alarm_cfg(self) -> dict:
        if self._settings is None:
            return {}
        return self._settings.get("alarm", {}) or {}

    def _open_settings_overlay(self) -> None:
        """Reset + open the alarm settings overlay (called from a swipe-down)."""
        if self._settings_screen is not None:
            self._settings_screen.open()
        self.open_settings()

    def _open_system_settings_overlay(self) -> None:
        """Reset + open the system settings overlay (called from a swipe-up)."""
        if self._system_settings_screen is not None:
            self._system_settings_screen.open()
        self.open_system_settings()

    def open_system_settings_at(self, section: str, sub: str | None = None) -> None:
        """Open the system settings overlay landed on a target section/sub-sheet.

        Used by the info screen's weather hints (Location sheet / Internet section).
        Safe from any thread.
        """
        if self._system_settings_screen is not None:
            self._system_settings_screen.open(section=section, sub=sub)
        self.open_system_settings()

    def _enter_info(self) -> None:
        """Switch to the info screen (called from a single-finger left swipe)."""
        if self._info_screen is not None:
            self._info_screen.open()
        self.set_mode(DisplayMode.INFO)

    def _active_screen(self):
        """The screen object for the open overlay, or ``None``."""
        ov = self.active_overlay
        if ov == "alarm":
            return self._settings_screen
        if ov == "system":
            return self._system_settings_screen
        return None

    def _track_swipe(self, event, current_mode) -> None:
        """Detect a two-finger swipe to open a settings overlay.

        Swipe DOWN opens the alarm settings; swipe UP opens the system settings.
        The settings overlays are reachable from every visible screen — the avatar
        face, the info screen, and the night-mode clock — so this runs in all of
        them. Only the fully blank (screen-off) display ignores it. Running in
        every (non-blank) mode also means a finger is always popped on its
        FINGERUP no matter which screen the touch ends on, so no stale entry can
        block later detection.
        """
        if current_mode == DisplayMode.BLANK:
            self._swipe_fingers.clear()
            return
        if event.type == pygame.FINGERDOWN:
            self._swipe_fingers[event.finger_id] = [event.y, event.y]
        elif event.type == pygame.FINGERMOTION:
            f = self._swipe_fingers.get(event.finger_id)
            if f is not None:
                f[1] = event.y
                if len(self._swipe_fingers) >= 2:
                    deltas = [cur - y0 for y0, cur in self._swipe_fingers.values()]
                    if all(d > SWIPE_DOWN_THRESHOLD for d in deltas):
                        self._swipe_fingers.clear()
                        self._open_settings_overlay()
                    elif all(d < -SWIPE_UP_THRESHOLD for d in deltas):
                        self._swipe_fingers.clear()
                        self._open_system_settings_overlay()
        elif event.type == pygame.FINGERUP:
            self._swipe_fingers.pop(event.finger_id, None)

    @staticmethod
    def _norm_xy(event):
        """Normalized 0..1 (x, y) for a finger/mouse event, or None."""
        if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            return event.x, event.y
        if hasattr(event, "pos"):
            return event.pos[0] / SCREEN_SIZE, event.pos[1] / SCREEN_SIZE
        return None

    def _track_h_swipe(self, event, current_mode, now) -> None:
        """Single-finger horizontal swipe + deferred-tap bookkeeping.

        Strictly one-finger: the moment a second finger lands the gesture is
        cancelled, so a two-finger settings swipe (which the other finger's motion
        would otherwise read as a big horizontal delta) can never be mistaken for
        a left/right swipe. Left commits only in AVATAR (-> INFO); right only in
        INFO (-> AVATAR). The gesture also records the down timestamp and whether
        the touch moved past ``TAP_RADIUS``, so the event loop fires a laugh/
        double-tap on release *only* when the touch stayed a single-finger tap.
        """
        t = event.type
        if t == pygame.FINGERDOWN:
            self._h_fingers.add(event.finger_id)
            if len(self._h_fingers) > 1:
                # Multi-touch: this is a two-finger gesture, not a swipe/tap.
                if self._h_gesture is not None:
                    self._h_gesture["cancel"] = True
                return
            self._h_gesture = {"fid": event.finger_id, "x0": event.x, "y0": event.y,
                               "down_ts": now, "moved": False, "fired": False, "cancel": False}
        elif t == pygame.MOUSEBUTTONDOWN:
            xy = self._norm_xy(event)
            if xy is not None:
                self._h_gesture = {"fid": None, "x0": xy[0], "y0": xy[1], "down_ts": now,
                                   "moved": False, "fired": False, "cancel": False}
        elif t in (pygame.FINGERMOTION, pygame.MOUSEMOTION):
            g = self._h_gesture
            if g is None or g["cancel"]:
                return
            # Only the originating finger drives the gesture, and only while it's
            # the sole finger down.
            if t == pygame.FINGERMOTION and (event.finger_id != g["fid"]
                                             or len(self._h_fingers) > 1):
                return
            xy = self._norm_xy(event)
            if xy is None:
                return
            dx, dy = xy[0] - g["x0"], xy[1] - g["y0"]
            if abs(dx) > TAP_RADIUS or abs(dy) > TAP_RADIUS:
                g["moved"] = True
            if (not g["fired"] and abs(dx) > H_SWIPE_THRESHOLD
                    and abs(dy) < H_SWIPE_MAX_OFF_AXIS):
                if dx < 0 and current_mode == DisplayMode.AVATAR:
                    g["fired"] = True
                    self._enter_info()
                elif dx > 0 and current_mode == DisplayMode.INFO:
                    g["fired"] = True
                    self.set_mode(DisplayMode.AVATAR)
        elif t == pygame.FINGERUP:
            self._h_fingers.discard(event.finger_id)

    def _reset_h_gesture(self) -> None:
        """Drop in-flight single-finger gesture state.

        Called whenever input is routed away from the swipe trackers — while a
        settings overlay owns input, and on wake-from-park. In those windows the
        ``FINGERUP`` that ends a (two-finger) gesture is consumed elsewhere and
        never reaches :meth:`_track_h_swipe`, so without this the ``_h_fingers``
        set accumulates stale ids (touch ids are never reused) and every later
        single-finger swipe trips the multi-touch guard.
        """
        self._h_fingers.clear()
        self._h_gesture = None
        self._swipe_fingers.clear()

    def start(self) -> None:
        """Start the avatar display in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._stop_event.clear()
        # Sample CPU temperature from boot so the info screen has a previous-minute
        # average ready before it's ever opened.
        try:
            if self._cpu_monitor is None:
                self._cpu_monitor = CpuTempMonitor()
            self._cpu_monitor.start()
        except Exception as e:
            logger.warning(f"CPU temp monitor failed to start: {e}")
        self._thread = threading.Thread(target=self._run, daemon=True, name="dodi-avatar")
        self._thread.start()
        logger.info("Avatar display started")

    def stop(self) -> None:
        """Stop the avatar display."""
        self._running = False
        self._stop_event.set()
        if self._cpu_monitor is not None:
            self._cpu_monitor.stop()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("Avatar display stopped")

    def trigger_laugh(self) -> None:
        """Trigger the laugh animation (e.g. from touch event)."""
        self._laugh_event.set()

    def pause_render(self) -> None:
        """Stop drawing/flipping (input is still pumped). Safe from any thread.

        Used by the idle display-off feature. The current frame stays in the
        framebuffer; the underlying mode is untouched, so resuming continues
        exactly where it left off.
        """
        self._render_paused = True

    def resume_render(self) -> None:
        """Resume drawing after :meth:`pause_render`. Safe from any thread."""
        self._render_paused = False

    def _render_frame(self, screen, current_mode, settings_open, state) -> None:
        """Draw one full frame for the current mode (no flip)."""
        active_screen = self._active_screen()
        if settings_open and active_screen is not None:
            active_screen.draw(screen)
        elif current_mode == DisplayMode.AVATAR:
            screen.fill(COL_BG)
            _draw_bird_avatar(screen, state, self.deaf)
        elif current_mode == DisplayMode.INFO:
            if self._info_screen is not None:
                self._info_screen.draw(screen)
            else:
                screen.fill(COL_BG)
        elif current_mode == DisplayMode.CLOCK:
            screen.fill((0, 0, 0))
            _draw_clock(screen, self._clock_font, self._clock_sub_font,
                        self._alarm_cfg(), self.active_alarm)
        else:  # BLANK
            screen.fill((0, 0, 0))

    def _run(self) -> None:
        """Main display loop running in background thread."""
        # Force SDL to use KMS/DRM for headless Pi with DSI display
        if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "KMSDRM"

        # Try to set up the display
        try:
            screen = pygame.display.set_mode(
                (SCREEN_SIZE, SCREEN_SIZE), pygame.FULLSCREEN | pygame.NOFRAME
            )
        except pygame.error:
            # Fallback to windowed mode for development
            try:
                screen = pygame.display.set_mode((SCREEN_SIZE, SCREEN_SIZE), pygame.NOFRAME)
            except pygame.error as e:
                logger.error(f"Failed to initialize display: {e}")
                return

        pygame.display.set_caption("DODI Bird Avatar")
        pygame.mouse.set_visible(False)
        clock = pygame.time.Clock()

        # Initialize fonts for the CLOCK screen (bundled Nunito).
        try:
            pygame.font.init()
            self._clock_font = theme.font(240, 900)     # big HH:MM
            self._clock_sub_font = theme.font(34, 700)  # next-alarm line
        except Exception as e:
            logger.warning(f"Could not initialize fonts: {e}")

        # Alarm settings screen (rendered + driven in this thread when open).
        try:
            self._settings_screen = SettingsScreen(self)
        except Exception as e:
            logger.warning(f"Could not init settings screen: {e}")
            self._settings_screen = None

        # System settings screen (swipe-up overlay).
        try:
            self._system_settings_screen = SystemSettingsScreen(self)
        except Exception as e:
            logger.warning(f"Could not init system settings screen: {e}")
            self._system_settings_screen = None

        # Info screen (single-finger left-swipe peer view).
        try:
            self._info_screen = InfoScreen(self)
        except Exception as e:
            logger.warning(f"Could not init info screen: {e}")
            self._info_screen = None

        # Animation state
        state = _AnimationState()

        # Render bookkeeping for the adaptive_fps path.
        last_sig = None      # signature of what's currently on screen
        was_paused = False   # detect the parked -> active transition

        try:
            while self._running:
                now = time.monotonic()
                current_mode = self.mode

                # Parked (idle display-off): don't draw at all — just pump input
                # so a tap can wake us. A tap sets only interaction_event (no
                # laugh / toggle / alarm-dismiss), so a wake touch only wakes.
                if self._render_paused:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            self._running = False
                        elif (event.type == pygame.KEYDOWN
                              and event.key == pygame.K_ESCAPE):
                            self._running = False
                        elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                            self._interaction_event.set()
                    was_paused = True
                    clock.tick(PAUSED_FPS)
                    continue

                # Just resumed: force a repaint and drop stale tap timing so the
                # wake touch can't pair into a double-tap.
                if was_paused:
                    was_paused = False
                    last_sig = None
                    self._last_tap_ts = 0.0
                    self._reset_h_gesture()  # park swallowed any in-flight touch

                # Process events (touch detection)
                settings_open = self.settings_active
                if settings_open:
                    # The overlay owns input now; the swipe trackers are dormant
                    # and can't keep finger bookkeeping in sync (the FINGERUPs that
                    # end a two-finger swipe land here and are swallowed). Reset so
                    # stale touch ids can't poison single-finger swipes once it
                    # closes.
                    self._reset_h_gesture()
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self._running = False
                        continue
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        self._running = False
                        continue

                    if settings_open:
                        # Whichever overlay is open owns all input while it's open.
                        scr = self._active_screen()
                        if scr is not None:
                            scr.handle_event(event)
                        continue

                    # Single-finger horizontal swipe: left -> info, right -> avatar.
                    # Also records the gesture so a tap fires on release (never
                    # mid-swipe). Any touch-down still registers as interaction.
                    self._track_h_swipe(event, current_mode, now)

                    if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                        self._interaction_event.set()

                    # Two-finger vertical swipe (from the face) opens settings.
                    # Run it in every mode so its finger bookkeeping stays coherent
                    # (it self-clears in INFO/BLANK) — otherwise a finger that goes
                    # down in AVATAR and up in INFO leaves a stale entry that blocks
                    # later two-finger detection.
                    self._track_swipe(event, current_mode)

                    if current_mode == DisplayMode.INFO:
                        # The info screen owns its taps (the weather hint); the
                        # swipe back to the avatar was handled above.
                        if self._info_screen is not None:
                            self._info_screen.handle_event(event)
                        continue

                    if event.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP):
                        # Fire the tap on release — but only if the touch never
                        # became a swipe, so a left-swipe can't also laugh. The
                        # double-tap window is still measured down-to-down.
                        g = self._h_gesture
                        if g is not None and not g["fired"] and not g["moved"] and not g["cancel"]:
                            if self._active_alarm is not None:
                                # An alarm is ringing: a tap always dismisses it,
                                # whatever the display mode (the mode can be AVATAR
                                # if night mode just lifted out from under it).
                                self._touch_event.set()
                            elif current_mode == DisplayMode.AVATAR:
                                kind, self._last_tap_ts = _classify_tap(
                                    self._last_tap_ts, g["down_ts"], DOUBLE_TAP_WINDOW
                                )
                                if kind == "double":
                                    self._double_tap_event.set()
                                else:
                                    self._laugh_event.set()
                            else:
                                # Night-mode CLOCK/BLANK: a tap dismisses the alarm.
                                self._touch_event.set()
                        self._h_gesture = None

                # Advance avatar animation every iteration (cheap) so motion is
                # correct even on frames whose draw we later skip.
                if (not settings_open) and current_mode == DisplayMode.AVATAR:
                    if self._laugh_event.is_set():
                        self._laugh_event.clear()
                        state.start_laugh(now)
                    state.update(now)

                adaptive = False
                if self._settings is not None:
                    adaptive = bool(
                        self._settings.get("power_options", {}).get("adaptive_fps", False)
                    )

                if adaptive:
                    # Repaint only when the visible content changed; idle the
                    # frame rate the rest of the time.
                    if settings_open and self._active_screen() is not None:
                        sig = None          # interactive overlay: always repaint
                        active = True
                    elif current_mode == DisplayMode.AVATAR:
                        sig = ("avatar", round(state.eye_openness, 3), state.laughing,
                               round(state._laugh_shake, 1), self.deaf)
                        active = state.is_animating()
                    elif current_mode == DisplayMode.INFO:
                        # Repaints when the minute, CPU average, theme, or weather
                        # result changes (the loop polls at IDLE_FPS).
                        sig = (self._info_screen.sig() if self._info_screen is not None
                               else ("info",))
                        active = False
                    elif current_mode == DisplayMode.CLOCK:
                        # HH:MM changes once a minute (which also rolls the
                        # next-alarm label) — a sufficient repaint key.
                        sig = ("clock", _dt.datetime.now().strftime("%H:%M"))
                        active = False
                    else:  # BLANK
                        sig = ("blank",)
                        active = False

                    if sig is None or sig != last_sig:
                        self._render_frame(screen, current_mode, settings_open, state)
                        pygame.display.flip()
                        last_sig = sig
                    clock.tick(ACTIVE_FPS if active else IDLE_FPS)
                else:
                    # Legacy fixed-rate path: always repaint at 30 FPS.
                    self._render_frame(screen, current_mode, settings_open, state)
                    pygame.display.flip()
                    last_sig = None
                    clock.tick(ACTIVE_FPS)

        except Exception as e:
            logger.error(f"Avatar display error: {e}")
        finally:
            pygame.quit()


class _AnimationState:
    """Tracks all animation state for the avatar face."""

    def __init__(self):
        self.now = time.monotonic()

        # Blink state
        self.eye_openness = 1.0
        self._blink_phase = "open"
        self._blink_timer = 0.0
        self._next_blink = self.now + random.uniform(2.0, 5.0)
        self._blink_speed = 0.08

        # Laugh state
        self.laughing = False
        self.laugh_progress = 0.0
        self._laugh_start = 0.0
        self._laugh_duration = 1.2
        self._laugh_shake = 0.0

    def update(self, now: float) -> None:
        self.now = now
        self._update_blink(now)
        self._update_laugh(now)

    def is_animating(self) -> bool:
        """True while a blink or laugh is in progress (drives ACTIVE_FPS)."""
        return self.laughing or self._blink_phase != "open"

    def start_laugh(self, now: float) -> None:
        if not self.laughing:
            self.laughing = True
            self._laugh_start = now
            self.laugh_progress = 0.0

    def _update_blink(self, now: float) -> None:
        if self._blink_phase == "open":
            if not self.laughing and now >= self._next_blink:
                self._blink_phase = "closing"
                self._blink_timer = now
            return

        elapsed = now - self._blink_timer

        if self._blink_phase == "closing":
            self.eye_openness = max(0.0, 1.0 - elapsed / self._blink_speed)
            if self.eye_openness <= 0.0:
                self._blink_phase = "closed"
                self._blink_timer = now

        elif self._blink_phase == "closed":
            if elapsed > 0.05:
                self._blink_phase = "opening"
                self._blink_timer = now

        elif self._blink_phase == "opening":
            self.eye_openness = min(1.0, elapsed / self._blink_speed)
            if self.eye_openness >= 1.0:
                self._blink_phase = "open"
                if random.random() < 0.2:
                    self._next_blink = now + random.uniform(0.15, 0.3)
                else:
                    self._next_blink = now + random.uniform(2.5, 6.0)

    def _update_laugh(self, now: float) -> None:
        if not self.laughing:
            self._laugh_shake = 0.0
            return

        elapsed = now - self._laugh_start
        if elapsed >= self._laugh_duration:
            self.laughing = False
            self.laugh_progress = 0.0
            self._laugh_shake = 0.0
            return

        t = elapsed / self._laugh_duration

        if t < 0.15:
            self.laugh_progress = t / 0.15
        elif t < 0.7:
            self.laugh_progress = 1.0
        else:
            self.laugh_progress = 1.0 - (t - 0.7) / 0.3

        if t < 0.8:
            self._laugh_shake = math.sin(now * 25) * 4 * self.laugh_progress
        else:
            self._laugh_shake = 0.0


# ---------------------------------------------------------------------------
# Drawing functions
# ---------------------------------------------------------------------------

def _draw_clock(screen, font, sub_font=None, alarm_cfg=None, active_alarm=None) -> None:
    """Render the 24h time, plus a subtle next-alarm line at the bottom.

    While an alarm is ringing, ``active_alarm`` pins that ring (an nr-shaped
    dict) so the line keeps showing it instead of rolling to "Tomorrow" once
    the clock ticks past the alarm minute.
    """
    if font is None:
        return
    text = _dt.datetime.now().strftime("%H:%M")
    surface = font.render(text, True, (255, 255, 255))
    rect = surface.get_rect(center=(CX, CY - 20))
    screen.blit(surface, rect)

    # Subtle next-alarm indicator: [alarm icon]  Today · 06:30
    if sub_font is None:
        return
    nr = active_alarm or (next_ring(alarm_cfg, _dt.datetime.now()) if alarm_cfg else None)
    if nr is None:
        return
    muted = (120, 132, 146)
    day = nr["label"]
    label = f"{'One-time · ' if nr['once'] else ''}{day} · {nr['time']}"
    txt = sub_font.render(label, True, muted)
    icon_size = 30
    gap = 12
    total_w = icon_size + gap + txt.get_width()
    y = CY + 170
    x0 = CX - total_w // 2
    theme.draw_alarm_icon(screen, x0 + icon_size // 2, y, icon_size, muted, width=3)
    screen.blit(txt, (x0 + icon_size + gap, y - txt.get_height() // 2))


_headphones_surface: pygame.Surface | None = None
_headphones_loaded = False


def _get_headphones() -> pygame.Surface | None:
    """Load, scale and cache the DEAF-mode headphones overlay (once)."""
    global _headphones_surface, _headphones_loaded
    if _headphones_loaded:
        return _headphones_surface
    _headphones_loaded = True
    try:
        img = pygame.image.load(str(_HEADPHONES_PATH))
    except (pygame.error, OSError) as e:
        logger.warning("Headphones overlay not found at %s: %s", _HEADPHONES_PATH, e)
        return None
    w, h = img.get_size()
    scaled_h = max(1, round(h * HP_WIDTH / w))
    img = pygame.transform.smoothscale(img, (HP_WIDTH, scaled_h))
    # convert_alpha() speeds up blits but needs a display surface; skip it headless.
    if pygame.display.get_surface() is not None:
        try:
            img = img.convert_alpha()
        except pygame.error:
            pass
    _headphones_surface = img
    return img


def _draw_headphones(screen, sx: int = 0, sy: int = 0) -> None:
    """Overlay the headphones image, indicating DEAF (microphone-off) mode."""
    surf = _get_headphones()
    if surf is None:
        return
    sw, sh = surf.get_size()
    left = (SCREEN_SIZE - sw) // 2 + sx
    top = HP_EARCUP_Y - round(HP_EARCUP_FRAC * sh) + sy
    screen.blit(surf, (left, top))


def _draw_bird_avatar(screen, state: _AnimationState, deaf: bool = False) -> None:
    """Draw the inner bird face with bigger eyes and a better beak."""
    sx = int(state._laugh_shake)
    sy = int(abs(state._laugh_shake) * 0.5) if state.laughing else 0

    # --- Face Features ---

    # Cheek shadows/blush (kept soft)
    pygame.draw.ellipse(screen, COL_FACE_SHADOW, (CX - 260 + sx, CY + 80 + sy, 120, 80))
    pygame.draw.ellipse(screen, COL_FACE_SHADOW, (CX + 140 + sx, CY + 80 + sy, 120, 80))

    # --- Eyes (Bigger) ---
    _draw_bird_eye(screen, CX - EYE_X_OFFSET + sx, EYE_Y + sy, EYE_WIDTH, EYE_HEIGHT, state.eye_openness, state.laughing)
    _draw_bird_eye(screen, CX + EYE_X_OFFSET + sx, EYE_Y + sy, EYE_WIDTH, EYE_HEIGHT, state.eye_openness, state.laughing)

    # --- Beak (Redesigned) ---
    by = BEAK_Y + sy

    # Lower beak (smaller, drawn first)
    pygame.draw.ellipse(screen, COL_BEAK_FILL, (CX - 70 + sx, by, 140, 80))

    # Upper beak (larger, drawn on top)
    pygame.draw.ellipse(screen, COL_BEAK_FILL, (CX - 100 + sx, by - 50, 200, 110))

    # Beak highlight (on upper beak)
    pygame.draw.ellipse(screen, COL_BEAK_HIGHLIGHT, (CX - 60 + sx, by - 30, 70, 40))

    # Mouth line / Beak separation curve
    # This defines the shape of the mouth between the upper and lower beak
    pygame.draw.arc(screen, COL_FACE_SHADOW,
                    (CX - 80 + sx, by - 20, 160, 60),
                    math.radians(200), math.radians(340), 4)

    # Headphones overlay indicates DEAF mode (mic off).
    if deaf:
        _draw_headphones(screen, sx, sy)


def _draw_bird_eye(screen, cx: int, cy: int, w: int, h: int, openness: float, laughing: bool) -> None:
    """Draw a single bird eye."""

    if laughing:
        # Happy "squinting" eye - thin dark line
        pygame.draw.arc(screen, COL_EYE_DARK, (cx - w//2, cy - h//2, w, h), math.radians(20), math.radians(160), 5)
        return

    current_h = max(4, int(h * openness))
    eye_rect = pygame.Rect(cx - w // 2, cy - current_h // 2, w, current_h)

    if openness < 0.1:
        # Closed eye - thin dark line
        pygame.draw.line(screen, COL_EYE_DARK, (cx - w // 2, cy), (cx + w // 2, cy), 3)
        return

    # Main eye ball (dark fill)
    pygame.draw.ellipse(screen, COL_EYE_DARK, eye_rect)

    if openness > 0.4:
        # Iris - scaled up with the eye
        iris_h = int(current_h * 0.55)
        iris_w = int(w * 0.55)
        iris_rect = pygame.Rect(cx - iris_w // 2, cy + current_h // 2 - iris_h - 10, iris_w, iris_h)
        screen.set_clip(eye_rect)
        pygame.draw.ellipse(screen, COL_EYE_IRIS, iris_rect)
        screen.set_clip(None)

        # Highlights (sparkles) - scaled up and repositioned
        # Top-left large
        pygame.draw.circle(screen, COL_EYE_HIGHLIGHT, (cx - w // 4, cy - current_h // 4), 16)
        # Top-right large
        pygame.draw.circle(screen, COL_EYE_HIGHLIGHT, (cx + w // 4, cy - current_h // 4), 16)
        # Bottom-left small
        pygame.draw.circle(screen, COL_EYE_HIGHLIGHT, (cx - w // 4 + 15, cy + current_h // 4), 8)
        # Bottom-right small
        pygame.draw.circle(screen, COL_EYE_HIGHLIGHT, (cx + w // 4 - 15, cy + current_h // 4), 8)