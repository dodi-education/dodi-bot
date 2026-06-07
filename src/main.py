#!/usr/bin/env python3
"""
DODI - Didactic Doing Robot Companion
Main entry point for the application.

DODI runs a native, full-duplex live conversation with a cloud AI provider
(e.g. Gemini Live). It has two modes:

* DEAF   — the microphone is off (privacy default). The avatar wears headphones.
* NORMAL — the microphone is on and audio is streamed to/from the AI provider.

The screen is double-tapped to toggle modes. NORMAL drops back to DEAF after a
configurable inactivity timeout, or immediately on another double-tap.
"""

import sys
import os
import signal
import logging
import argparse
import asyncio
import time
from pathlib import Path
from typing import Optional


# Auto-activate virtual environment if not already active
def ensure_venv():
    """Re-exec using venv Python if not already in the virtual environment."""
    venv_path = Path(__file__).parent.parent / "venv"
    venv_python = venv_path / "bin" / "python"

    # Check if we're already in the project's venv
    in_venv = hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix
    in_project_venv = in_venv and Path(sys.prefix).resolve() == venv_path.resolve()

    if not in_project_venv:
        if venv_python.exists():
            # Re-execute with venv Python
            import os
            os.execv(str(venv_python), [str(venv_python)] + sys.argv)
        else:
            # Venv doesn't exist - tell user to run install
            print("Virtual environment not found.")
            print("Please run: sudo ./scripts/install.sh")
            sys.exit(1)


ensure_venv()

# Now imports will use venv packages
from dotenv import load_dotenv

from audio import AudioInput, AudioOutput
from streaming.factory import create_streaming_provider, is_provider_available
from conversation import InactivityTimer, Mode
from display import AvatarDisplay, detect_screen
from night_mode import NightModeManager
from night_mode.display_power import set_brightness, set_power
from alarm.manager import AlarmManager
from settings_store import load_settings, save_settings

# Load environment variables
PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(PROJECT_DIR / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("DODI")


class DODI:
    """Main DODI application: a two-mode live-conversation companion."""

    def __init__(self, settings: dict, debug: bool = False):
        self.settings = settings
        self.running = False
        self.debug = debug

        # Audio settings
        audio_config = settings.get("audio", {})
        self.sample_rate = audio_config.get("sample_rate", 16000)
        self.device_name = audio_config.get("input_device")
        self.volume = audio_config.get("volume", 100)

        # Streaming config (the live conversation provider)
        streaming_config = settings.get("streaming", {})
        self.use_streaming = streaming_config.get("enabled", True)
        self.streaming_provider_name = streaming_config.get("active", "gemini")
        self.streaming_provider_config = streaming_config.get(self.streaming_provider_name, {})

        # Inactivity timeout: NORMAL -> DEAF after this many idle seconds.
        conversation_config = settings.get("conversation", {})
        self.active_timeout = conversation_config.get("timeout_seconds", 10.0)

        # Idle display-off (power_options). null/None => disabled in that context.
        power_cfg = settings.get("power_options", {})
        self._turn_off_day = power_cfg.get("turn_off_display")             # None | seconds
        self._turn_off_night = power_cfg.get("turn_off_display_night_mode")  # None | seconds
        self._last_interaction = time.monotonic()
        self._display_off = False
        self._display_context = "day"  # "day" | "night" | "alarm" | "settings"

        # Runtime state
        self._mode = Mode.DEAF
        self._timer = InactivityTimer(self.active_timeout)
        self._provider = None
        self._persona: str = ""

        # Display (wired up by main())
        self._avatar: Optional[AvatarDisplay] = None
        # Night mode + alarm engine (wired up by main())
        self._night_mode: Optional[NightModeManager] = None
        self._alarm = None

        # Audio components (lazy; also used by night_mode for the alarm)
        self._audio_input: Optional[AudioInput] = None
        self._audio_output: Optional[AudioOutput] = None

    # --------------------------------------------------------------- helpers

    def _init_audio_input(self) -> AudioInput:
        """Initialize (but do not start) the microphone capture."""
        if self._audio_input is None:
            self._audio_input = AudioInput(
                sample_rate=self.sample_rate,
                device_name=self.device_name,
            )
        return self._audio_input

    def _init_audio_output(self) -> AudioOutput:
        """Initialize audio output (used by night_mode for the alarm)."""
        if self._audio_output is None:
            self._audio_output = AudioOutput(volume=self.volume)
        return self._audio_output

    def apply_setting(self, key: str, value) -> None:
        """Live-apply a change from the system settings UI.

        The UI has already written ``value`` into the shared settings dict and saved
        it; this makes the *running* app honor it immediately for the keys that would
        otherwise only take effect on restart. Keys not handled here need no live hook
        (they are re-read from the dict when next used). Always best-effort.
        """
        try:
            if key == "audio.volume":
                v = int(value)
                self.volume = v
                if self._audio_output is not None:
                    self._audio_output.volume = v
                if self._provider is not None:
                    # Affects the openai path immediately; gemini's per-session output
                    # picks it up on the next activation (fine for a slider).
                    try:
                        self._provider.volume = v
                    except Exception:
                        pass
            elif key == "power_options.turn_off_display":
                self._turn_off_day = value            # None | seconds; read each tick
            elif key == "power_options.turn_off_display_night_mode":
                self._turn_off_night = value
            elif key == "night_mode.display.brightness":
                mgr = self._night_mode
                if mgr is not None:
                    mgr._brightness = int(value)
                    mgr.reapply_display()             # no-op unless a night session is active
            elif key == "night_mode.schedule":
                mgr = self._night_mode
                if mgr is not None:
                    mgr._schedule = value             # _run re-reads it within <=60s
            elif key == "night_mode.enabled":
                mgr = self._night_mode
                if mgr is not None:
                    mgr.set_enabled(bool(value))      # live start/stop of the scheduler
        except Exception as e:
            logger.warning(f"apply_setting({key!r}) failed: {e}")

    def load_persona(self) -> str:
        """Load the active persona text (used as the AI system prompt)."""
        if self._persona:
            return self._persona

        persona_config = self.settings.get("persona", {})
        persona_dir = PROJECT_DIR / persona_config.get("directory", "personas/")
        active_persona = persona_config.get("active", "default")
        persona_file = persona_dir / f"{active_persona}.txt"

        if persona_file.exists():
            self._persona = persona_file.read_text()
            logger.info(f"Loaded persona: {active_persona}")
        else:
            logger.warning(f"Persona file not found: {persona_file}")
            self._persona = ""

        return self._persona

    def _can_use_voice_streaming(self) -> bool:
        """True if streaming is enabled and the provider's API key is set."""
        return self.use_streaming and is_provider_available(self.streaming_provider_name)

    # ------------------------------------------------------- activity signals

    def _mark_activity(self) -> None:
        """Record voice activity: feeds both the DEAF timeout and display-idle."""
        self._timer.mark()
        self._last_interaction = time.monotonic()

    def _on_transcript(self, text: str) -> None:
        """Any transcript (user or AI) counts as conversation activity."""
        self._mark_activity()
        if self.debug and text.strip():
            print(f"[transcript] {text}")

    def _on_turn_end(self) -> None:
        """The AI finished a turn — recent activity, keep the session alive."""
        self._mark_activity()

    # ----------------------------------------------------------- mode changes

    async def _enter_normal(self) -> None:
        """DEAF -> NORMAL: mic on + connect to the live provider."""
        if self._mode == Mode.NORMAL:
            return
        if not self._can_use_voice_streaming():
            logger.error(
                "Voice streaming unavailable (disabled or missing API key for "
                f"'{self.streaming_provider_name}'). Staying DEAF."
            )
            return

        logger.info("Entering NORMAL mode (microphone on)")

        # Microphone on.
        audio_input = self._init_audio_input()
        audio_input.start()
        audio_input.clear_queue()

        # Fresh provider per activation (connect-on-activate).
        try:
            provider = create_streaming_provider(
                self.streaming_provider_name,
                self.streaming_provider_config,
                input_sample_rate=self.sample_rate,
                volume=self.volume,
            )
            await provider.start_session(
                system_prompt=self.load_persona(),
                on_transcript=self._on_transcript,
                on_end=self._on_turn_end,
            )
        except Exception as e:
            logger.error(f"Failed to start voice session: {e}")
            audio_input.stop()
            return

        self._provider = provider
        self._mode = Mode.NORMAL
        self._mark_activity()
        if self._avatar is not None:
            self._avatar.set_deaf(False)
        print("[DODI] NORMAL mode — talk to me. Double-tap to stop.")

    async def _enter_deaf(self) -> None:
        """NORMAL -> DEAF (also the startup state): disconnect + mic off.

        Idempotent and privacy-critical: always ends with the provider down,
        the microphone fully stopped, and the headphones shown.
        """
        if self._provider is not None:
            provider, self._provider = self._provider, None
            try:
                await provider.stop_session()
            except Exception as e:
                logger.warning(f"Error stopping provider: {e}")

        if self._audio_input is not None:
            # stop() joins the capture thread + tears down PyAudio; run it off
            # the event loop so it can't stall the receive task / playback.
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._audio_input.stop)
            except Exception as e:
                logger.warning(f"Error stopping microphone: {e}")

        self._timer.clear()
        self._mode = Mode.DEAF
        if self._avatar is not None:
            self._avatar.set_deaf(True)
        logger.info("DEAF mode (microphone off)")

    # ----------------------------------------------------------- control loop

    async def _pump_once(self, loop) -> None:
        """One full-duplex step in NORMAL mode: feed mic -> provider."""
        provider = self._provider
        if provider is None:
            return

        # The AI actively speaking keeps the session alive (don't time out in
        # the middle of a response).
        if provider.is_speaking:
            self._mark_activity()

        # Read one mic chunk without blocking the event loop (so the receive
        # task and audio playback keep running).
        chunk = await loop.run_in_executor(None, self._audio_input.read, 0.05)
        if chunk and not provider.is_speaking:
            # Echo guard: don't feed the speaker's output back in while the AI
            # is talking (provider tracks is_speaking + a short cooldown).
            # Note: we deliberately do NOT reset the timeout on raw mic level —
            # ambient noise would keep it alive forever. The timeout resets on
            # real conversation turns (user/AI transcripts, turn-complete).
            await provider.send_audio(chunk)

        await asyncio.sleep(0.01)

    def _handle_settings_backlight(self, opened: bool) -> None:
        """Light the screen fully while settings is open; restore on close.

        Lets the settings overlay be readable even if night mode dimmed the
        backlight. On close, hands brightness back to a ringing alarm or the
        night-mode display.
        """
        try:
            if opened:
                set_power(True)
                set_brightness(100)
            elif self._alarm is not None and self._alarm.is_ringing():
                return  # the alarm owns the display/brightness
            elif self._night_mode is not None and self._night_mode.is_active():
                self._night_mode.reapply_display()
        except Exception as e:
            logger.warning(f"Settings backlight error: {e}")

    def _tick_display_power(self) -> None:
        """Idle display-off: park the screen after no interaction, wake on touch.

        Runs every control-loop iteration (including while night/alarm preempt the
        loop). Voice activity resets the countdown via :meth:`_mark_activity`; a
        parked screen is woken only by a touch (audio is never touched here).
        """
        av = self._avatar
        if av is None:
            return
        now = time.monotonic()

        # A screen tap is interaction — and the only thing that wakes a park.
        touched = av.interaction_event.is_set()
        if touched:
            av.interaction_event.clear()
            self._last_interaction = now

        alarm_ringing = self._alarm is not None and self._alarm.is_ringing()
        settings_open = av.settings_active
        night = self._night_mode is not None and self._night_mode.is_active()
        context = ("alarm" if alarm_ringing else
                   "settings" if settings_open else
                   "night" if night else "day")

        # Ownership handoff (night ends, alarm starts, …): un-park if needed and
        # restart the countdown fresh for the new context.
        if context != self._display_context:
            self._display_context = context
            self._last_interaction = now
            if self._display_off:
                self._wake_display(night)

        # The alarm and settings overlay own the screen outright; never park
        # beneath them (they manage their own mode/brightness).
        if alarm_ringing or settings_open:
            self._last_interaction = now
            return

        timeout = self._turn_off_night if night else self._turn_off_day

        if self._display_off:
            if touched:  # touch-only wake
                self._wake_display(night)
            return

        if timeout is None:  # disabled in this context
            return

        if now - self._last_interaction >= timeout:
            self._sleep_display(night)

    def _sleep_display(self, night: bool) -> None:
        """Park the screen: backlight off + rendering stopped (mode untouched)."""
        try:
            set_power(False)
        except Exception as e:
            logger.warning(f"Display park error: {e}")
        if self._avatar is not None:
            self._avatar.pause_render()
        self._display_off = True
        logger.info("Display idle — parked (render off, backlight off)")

    def _wake_display(self, night: bool) -> None:
        """Wake a parked screen, resuming where it left off."""
        self._display_off = False
        if self._avatar is not None:
            self._avatar.resume_render()
        try:
            if night and self._night_mode is not None:
                self._night_mode.reapply_display()  # CLOCK + night brightness
            else:
                set_power(True)
                set_brightness(100)
        except Exception as e:
            logger.warning(f"Display wake error: {e}")
        self._last_interaction = time.monotonic()
        logger.info("Display woken")

    def _is_preempted(self) -> bool:
        """True when night mode, a ringing alarm, or settings owns the device."""
        if self._night_mode is not None and self._night_mode.is_active():
            return True
        if self._alarm is not None and self._alarm.is_ringing():
            return True
        if self._avatar is not None and self._avatar.settings_active:
            return True
        return False

    def _request_stop(self) -> None:
        """Signal handler: stop gracefully; a second signal exits hard."""
        if not self.running:
            print("\nForce exit.", flush=True)
            os._exit(130)
        print("\nShutting down... (press Ctrl+C again to force)", flush=True)
        self.running = False

    async def _run_async(self) -> None:
        """The single long-lived control loop hosting the live session."""
        self.running = True
        loop = asyncio.get_running_loop()

        # Graceful Ctrl-C / SIGTERM: flip the loop to exit and return to DEAF
        # (cleanly closing the audio stream). A second signal forces a hard exit
        # if cleanup ever wedges, so the process is always killable.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except (NotImplementedError, RuntimeError):
                pass  # signal handlers not available on this platform

        # Start DEAF (microphone off, headphones on).
        await self._enter_deaf()

        settings_was_open = False
        try:
            while self.running:
                # Idle display-off / wake-on-touch (runs in every context,
                # including while night mode / an alarm preempt the loop below).
                self._tick_display_power()

                # Manage the backlight around the settings overlay (so it's
                # readable even when night mode has dimmed the screen).
                settings_open = self._avatar is not None and self._avatar.settings_active
                if settings_open != settings_was_open:
                    self._handle_settings_backlight(settings_open)
                    settings_was_open = settings_open

                # Night mode, a ringing alarm, or the open settings overlay each
                # take over the device — pause the conversation loop (mic off)
                # while any of them is active.
                if self._is_preempted():
                    if self._mode == Mode.NORMAL:
                        await self._enter_deaf()
                    await asyncio.sleep(0.2)
                    continue

                # Double-tap toggles DEAF <-> NORMAL.
                if self._avatar is not None and self._avatar.double_tap_event.is_set():
                    self._avatar.double_tap_event.clear()
                    if self._mode == Mode.DEAF:
                        await self._enter_normal()
                    else:
                        await self._enter_deaf()
                    continue

                if self._mode == Mode.DEAF:
                    await asyncio.sleep(0.05)
                    continue

                # NORMAL: pump audio, then check liveness + inactivity.
                await self._pump_once(loop)

                if self._provider is None or not self._provider.is_connected:
                    logger.warning("Provider connection lost - returning to DEAF")
                    await self._enter_deaf()
                elif self._timer.expired():
                    logger.info("Inactivity timeout - returning to DEAF")
                    await self._enter_deaf()
        finally:
            try:
                await self._enter_deaf()
            except Exception as e:
                logger.warning(f"Error during shutdown: {e}")

    def run(self) -> None:
        """Synchronous entry point: start the asyncio control loop."""
        print()
        print("=" * 50)
        print("  DODI is ready (starting in DEAF mode)")
        if self._can_use_voice_streaming():
            print(f"  Live provider: {self.streaming_provider_name}")
        else:
            print("  Live provider: UNAVAILABLE (check API key / streaming.enabled)")
        print(f"  Inactivity timeout: {self.active_timeout:.0f}s")
        print("  Double-tap the screen to start/stop talking.")
        print("  Press Ctrl+C to exit")
        print("=" * 50)
        print()

        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            print("\n\nShutting down...")
        finally:
            self.running = False
            # Hard guarantee the mic is off on the way out.
            if self._audio_input is not None:
                try:
                    self._audio_input.stop()
                except Exception:
                    pass


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="DODI - Didactic Doing Robot Companion"
    )
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug mode - show transcriptions"
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    print("=" * 50)
    print("  DODI - Didactic Doing Robot Companion")
    if args.debug:
        print("  [DEBUG MODE ENABLED]")
    print("=" * 50)
    print()

    # Load settings
    print("[1/4] Loading settings...")
    settings = load_settings()
    if not settings:
        print("  [-] Could not load settings")
        return 1
    print("  [+] Settings loaded")

    # Create DODI instance
    print("[2/4] Initializing components...")
    try:
        dodi = DODI(settings, debug=args.debug)
        print("  [+] Components initialized")
    except Exception as e:
        print(f"  [-] Initialization failed: {e}")
        logger.exception("Initialization error")
        return 1

    # Detect and start display
    print("[3/4] Checking for display...")
    if detect_screen():
        print("  [+] Round display detected - starting avatar")
        dodi._avatar = AvatarDisplay()
        dodi._avatar.bind_settings(settings, save_settings, dodi.apply_setting)
        dodi._avatar.start()
        dodi._avatar.set_deaf(True)  # boot showing headphones (DEAF)
    else:
        print("  [-] No display detected - running headless")

    # Night mode manager. Always constructed when the config block exists (so the
    # settings UI can enable/disable it live); start() is a no-op while disabled.
    night_cfg = settings.get("night_mode")
    if night_cfg is not None:
        dodi._night_mode = NightModeManager(night_cfg, dodi._avatar, dodi)
        dodi._night_mode.start()
        print("  [+] Night mode " + ("enabled" if night_cfg.get("enabled") else "ready (disabled)"))

    # Alarm engine (decoupled from night mode; reads the `alarm` config block)
    if settings.get("alarm") is not None:
        dodi._alarm = AlarmManager(
            settings, dodi._avatar, dodi, save_settings, night_mode=dodi._night_mode
        )
        dodi._alarm.start()
        print("  [+] Alarm engine enabled")

    # Run main loop
    print("[4/4] Starting DODI...")
    try:
        dodi.run()
    except Exception as e:
        print(f"Error: {e}")
        logger.exception("Runtime error")
        return 1
    finally:
        if dodi._alarm:
            dodi._alarm.stop()
        if dodi._night_mode:
            dodi._night_mode.stop()
        if dodi._avatar:
            dodi._avatar.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
