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

import pygame

from . import theme
from .settings_screen import SettingsScreen
from alarm.schedule import next_ring

logger = logging.getLogger("DODI.display")


class DisplayMode(Enum):
    AVATAR = "avatar"
    CLOCK = "clock"
    BLANK = "blank"

# Two taps within this many seconds count as a double-tap (mode toggle).
DOUBLE_TAP_WINDOW = 0.30

# Two fingers dragging down by this much (normalized) opens alarm settings.
SWIPE_DOWN_THRESHOLD = 0.18

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
COL_HEADPHONE = (45, 48, 58)          # Dark headphone shell (deaf mode)
COL_HEADPHONE_PAD = (95, 100, 120)    # Lighter ear-cushion pad

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
        self._last_tap_ts = 0.0
        self._swipe_fingers: dict = {}  # finger_id -> [start_y, cur_y]
        self._mode_lock = threading.Lock()
        self._mode = DisplayMode.AVATAR
        self._deaf = False
        self._settings_active = False
        self._clock_font: pygame.font.Font | None = None
        self._clock_sub_font: pygame.font.Font | None = None
        # Shared settings + persistence (bound by main(); used by the clock's
        # next-alarm line and the settings screen).
        self._settings: dict | None = None
        self._save_settings = None
        self._settings_screen = None

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
    def settings_active(self) -> bool:
        """True while the alarm settings overlay is open."""
        with self._mode_lock:
            return self._settings_active

    def open_settings(self) -> None:
        """Open the alarm settings overlay (safe from any thread)."""
        with self._mode_lock:
            self._settings_active = True

    def close_settings(self) -> None:
        """Close the alarm settings overlay (safe from any thread)."""
        with self._mode_lock:
            self._settings_active = False

    def bind_settings(self, settings: dict, save_cb) -> None:
        """Give the display access to the live settings dict + a save callback.

        Used by the clock's next-alarm line and the alarm settings screen.
        """
        self._settings = settings
        self._save_settings = save_cb

    def _alarm_cfg(self) -> dict:
        if self._settings is None:
            return {}
        return self._settings.get("alarm", {}) or {}

    def _open_settings_overlay(self) -> None:
        """Reset + open the alarm settings overlay (called from the swipe)."""
        if self._settings_screen is not None:
            self._settings_screen.open()
        self.open_settings()

    def _track_swipe(self, event, current_mode) -> None:
        """Detect a two-finger swipe-down to open settings.

        Works on the avatar face AND the night-mode clock; only the fully blank
        (screen-off) display ignores it.
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
                if len(self._swipe_fingers) >= 2 and all(
                    (cur - y0) > SWIPE_DOWN_THRESHOLD
                    for y0, cur in self._swipe_fingers.values()
                ):
                    self._swipe_fingers.clear()
                    self._open_settings_overlay()
        elif event.type == pygame.FINGERUP:
            self._swipe_fingers.pop(event.finger_id, None)

    def start(self) -> None:
        """Start the avatar display in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dodi-avatar")
        self._thread.start()
        logger.info("Avatar display started")

    def stop(self) -> None:
        """Stop the avatar display."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("Avatar display stopped")

    def trigger_laugh(self) -> None:
        """Trigger the laugh animation (e.g. from touch event)."""
        self._laugh_event.set()

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

        # Animation state
        state = _AnimationState()

        try:
            while self._running:
                now = time.monotonic()
                current_mode = self.mode

                # Process events (touch detection)
                settings_open = self.settings_active
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self._running = False
                        continue
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        self._running = False
                        continue

                    if settings_open:
                        # The settings overlay owns all input while it's open.
                        if self._settings_screen is not None:
                            self._settings_screen.handle_event(event)
                        continue

                    # Two-finger swipe-down (from the face) opens settings.
                    self._track_swipe(event, current_mode)

                    if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                        kind, self._last_tap_ts = _classify_tap(
                            self._last_tap_ts, now, DOUBLE_TAP_WINDOW
                        )
                        if current_mode == DisplayMode.AVATAR:
                            # Double-tap toggles DEAF/NORMAL; a lone tap laughs.
                            if kind == "double":
                                self._double_tap_event.set()
                            else:
                                self._laugh_event.set()
                        else:
                            # Night-mode CLOCK/BLANK: any tap dismisses the alarm.
                            self._touch_event.set()

                if settings_open and self._settings_screen is not None:
                    self._settings_screen.draw(screen)
                elif current_mode == DisplayMode.AVATAR:
                    # Check for externally triggered laugh
                    if self._laugh_event.is_set():
                        self._laugh_event.clear()
                        state.start_laugh(now)

                    # Update animation state
                    state.update(now)

                    # Fill screen with face color (acts as the face background)
                    screen.fill(COL_BG)
                    _draw_bird_avatar(screen, state, self.deaf)
                elif current_mode == DisplayMode.CLOCK:
                    screen.fill((0, 0, 0))
                    _draw_clock(screen, self._clock_font, self._clock_sub_font,
                                self._alarm_cfg())
                else:  # BLANK
                    screen.fill((0, 0, 0))

                pygame.display.flip()
                clock.tick(30)

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

def _draw_clock(screen, font, sub_font=None, alarm_cfg=None) -> None:
    """Render the 24h time, plus a subtle next-alarm line at the bottom."""
    if font is None:
        return
    text = _dt.datetime.now().strftime("%H:%M")
    surface = font.render(text, True, (255, 255, 255))
    rect = surface.get_rect(center=(CX, CY - 20))
    screen.blit(surface, rect)

    # Subtle next-alarm indicator: [alarm icon]  Today · 06:30
    if sub_font is None or not alarm_cfg:
        return
    nr = next_ring(alarm_cfg, _dt.datetime.now())
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


def _draw_headphones(screen, sx: int = 0, sy: int = 0) -> None:
    """Draw over-ear headphones, indicating DEAF (microphone-off) mode."""
    # Headband: a band of nested arcs arcing over the top of the head.
    for i in range(14):
        pygame.draw.arc(
            screen, COL_HEADPHONE,
            (CX - 340 + i + sx, CY - 330 + i + sy, 680 - 2 * i, 520 - 2 * i),
            math.radians(25), math.radians(155), 3,
        )
    # Ear cups on each side, with a lighter cushion pad inside.
    left = pygame.Rect(CX - 360 + sx, CY - 80 + sy, 80, 170)
    right = pygame.Rect(CX + 280 + sx, CY - 80 + sy, 80, 170)
    for cup in (left, right):
        pygame.draw.ellipse(screen, COL_HEADPHONE, cup)
        pygame.draw.ellipse(screen, COL_HEADPHONE_PAD, cup.inflate(-28, -60))


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