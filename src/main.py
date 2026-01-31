#!/usr/bin/env python3
"""
DODI - Didactic Doing Robot Companion
Main entry point for the application.
"""

import sys
import json
import logging
import time
import argparse
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
from stt import VoskSTT
from tts import PiperTTS
from ai.factory import create_provider

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
    """Main DODI application class."""

    def __init__(self, settings: dict, debug: bool = False):
        self.settings = settings
        self.running = False
        self.debug = debug

        # Wake words and stop words
        wake_config = settings.get("wake_word", {})
        self.wake_words = wake_config.get("phrases", [wake_config.get("phrase", "dodi")])
        if isinstance(self.wake_words, str):
            self.wake_words = [self.wake_words]
        self.wake_words = [w.lower() for w in self.wake_words]

        self.stop_words = [w.lower() for w in settings.get("stop_words", ["stop"])]

        # Audio settings
        audio_config = settings.get("audio", {})
        self.sample_rate = audio_config.get("sample_rate", 16000)
        self.device_name = audio_config.get("input_device")

        # Initialize components (lazy loading)
        self._audio_input: Optional[AudioInput] = None
        self._audio_output: Optional[AudioOutput] = None
        self._stt: Optional[VoskSTT] = None
        self._tts: Optional[PiperTTS] = None
        self._ai_provider = None
        self._persona: str = ""

    def _init_audio_input(self) -> AudioInput:
        """Initialize audio input."""
        if self._audio_input is None:
            self._audio_input = AudioInput(
                sample_rate=self.sample_rate,
                device_name=self.device_name,
            )
        return self._audio_input

    def _init_audio_output(self) -> AudioOutput:
        """Initialize audio output."""
        if self._audio_output is None:
            self._audio_output = AudioOutput()
        return self._audio_output

    def _init_stt(self) -> VoskSTT:
        """Initialize speech-to-text."""
        if self._stt is None:
            stt_config = self.settings.get("local_models", {}).get("stt", {})
            model_path = PROJECT_DIR / stt_config.get("model_path", "models/stt/vosk-model-small-en-us-0.15")
            self._stt = VoskSTT(str(model_path), self.sample_rate)
            self._stt.initialize()
        return self._stt

    def _init_tts(self) -> PiperTTS:
        """Initialize text-to-speech."""
        if self._tts is None:
            tts_config = self.settings.get("local_models", {}).get("tts", {})
            model_path = PROJECT_DIR / tts_config.get("model_path", "models/tts/en_US-lessac-medium.onnx")
            self._tts = PiperTTS(str(model_path))
            self._tts.initialize()
        return self._tts

    def _init_ai_provider(self):
        """Initialize AI provider."""
        if self._ai_provider is None:
            provider_config = self.settings.get("ai_provider", {})
            active = provider_config.get("active", "gemini")
            config = provider_config.get(active, {})
            self._ai_provider = create_provider(active, config)
        return self._ai_provider

    def load_persona(self) -> str:
        """Load the active persona."""
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

    def contains_wake_word(self, text: str) -> tuple[bool, str]:
        """
        Check if text contains a wake word.

        Returns:
            Tuple of (wake_word_found, text_after_wake_word)
        """
        text_lower = text.lower()
        for wake_word in self.wake_words:
            if wake_word in text_lower:
                # Get text after the wake word
                idx = text_lower.find(wake_word)
                after_wake = text[idx + len(wake_word):].strip()
                return True, after_wake
        return False, ""

    def contains_stop_word(self, text: str) -> bool:
        """Check if text contains a stop word."""
        text_lower = text.lower()
        return any(stop_word in text_lower for stop_word in self.stop_words)

    def speak(self, text: str) -> bool:
        """
        Speak text using TTS, listening for stop words to interrupt.

        Returns:
            True if completed, False if interrupted by stop word
        """
        tts = self._init_tts()
        stt = self._init_stt()
        audio_input = self._init_audio_input()

        # Start speaking in background
        piper_proc, aplay_proc = tts.speak_async(text)

        # Listen for stop words while speaking
        stt.reset()
        while tts.is_speaking(aplay_proc):
            audio_data = audio_input.read(timeout=0.1)
            if audio_data:
                result = stt.process_audio(audio_data)
                if result and self.contains_stop_word(result):
                    logger.info(f"Stop word detected: {result}")
                    tts.stop_speaking(piper_proc, aplay_proc)
                    return False  # Interrupted

        # Check final exit codes
        piper_proc.wait()
        return aplay_proc.returncode == 0

    def listen_for_command(self, timeout: float = 5.0) -> str:
        """
        Listen for a voice command after wake word.

        Args:
            timeout: Max seconds to wait for speech

        Returns:
            Transcribed command text
        """
        stt = self._init_stt()
        audio_input = self._init_audio_input()

        # Clear any buffered audio
        audio_input.clear_queue()
        stt.reset()

        start_time = time.time()
        silence_start = None
        command_text = ""

        logger.info("Listening for command...")

        while time.time() - start_time < timeout:
            audio_data = audio_input.read(timeout=0.1)
            if audio_data is None:
                continue

            # Process audio
            result = stt.process_audio(audio_data)
            if result:
                command_text = result
                silence_start = time.time()
                if self.debug:
                    print(f"[DEBUG] Command transcribed: {result}")
            else:
                # Check for partial results to detect ongoing speech
                partial = stt.get_partial()
                if partial:
                    silence_start = time.time()
                    if self.debug:
                        print(f"[DEBUG] Command partial: {partial}")
                elif silence_start and time.time() - silence_start > 1.0:
                    # 1 second of silence after speech - command complete
                    break

        # Get final result if we have partial speech
        if not command_text:
            command_text = stt.finalize()

        logger.info(f"Command heard: {command_text}")
        return command_text

    def process_command(self, command: str) -> str:
        """Send command to AI and get response."""
        if not command.strip():
            return "I didn't catch that. Could you say it again?"

        logger.info(f"Processing command: {command}")
        ai = self._init_ai_provider()
        persona = self.load_persona()

        try:
            if self.debug:
                print(f"[DEBUG] Sending to AI: {command}")
            response = ai.chat(command, system_prompt=persona)
            logger.info(f"AI response: {response}")
            if self.debug:
                print(f"[DEBUG] AI response: {response}")
            return response
        except Exception as e:
            logger.error(f"AI error: {e}")
            if self.debug:
                print(f"[DEBUG] AI error: {e}")
            return "Sorry, I'm having trouble thinking right now. Can you try again?"

    def run(self) -> None:
        """Main conversation loop."""
        print()
        print("=" * 50)
        print("  DODI is listening...")
        print(f"  Wake words: {', '.join(self.wake_words)}")
        print(f"  Stop words: {', '.join(self.stop_words)}")
        print("  Press Ctrl+C to exit")
        print("=" * 50)
        print()

        # Initialize components
        audio_input = self._init_audio_input()
        audio_output = self._init_audio_output()
        stt = self._init_stt()

        self.running = True
        audio_input.start()

        try:
            while self.running:
                # Read audio chunk
                audio_data = audio_input.read(timeout=0.1)
                if audio_data is None:
                    continue

                # Check if we're playing audio and user wants to interrupt
                if audio_output.is_playing():
                    result = stt.process_audio(audio_data)
                    if result:
                        if self.debug:
                            print(f"[DEBUG] During playback: {result}")
                        if self.contains_stop_word(result):
                            logger.info("Stop word detected - interrupting playback")
                            print("[INTERRUPTED]")
                            audio_output.interrupt()
                            stt.reset()
                    continue

                # Process audio for wake word detection
                result = stt.process_audio(audio_data)
                if not result:
                    # Check partial for faster wake word detection
                    partial = stt.get_partial()
                    if partial:
                        if self.debug:
                            print(f"[DEBUG] Partial: {partial}")
                        has_wake, _ = self.contains_wake_word(partial)
                        if has_wake:
                            # Wait for complete utterance
                            time.sleep(0.5)
                            result = stt.finalize()

                if result:
                    if self.debug:
                        print(f"[DEBUG] Transcribed: {result}")
                    has_wake, after_wake = self.contains_wake_word(result)
                    if has_wake:
                        logger.info(f"Wake word detected in: {result}")
                        if self.debug:
                            print(f"[DEBUG] Wake word found! After wake: '{after_wake}'")

                        # If there's already a command after the wake word, use it
                        if after_wake:
                            command = after_wake
                        else:
                            # Listen for the command
                            command = self.listen_for_command(timeout=5.0)

                        if command:
                            # Check for stop words in command
                            if self.contains_stop_word(command):
                                logger.info("Stop word in command - ignoring")
                                continue

                            # Get AI response
                            response = self.process_command(command)

                            # Speak the response
                            self.speak(response)

                        stt.reset()

        except KeyboardInterrupt:
            print("\n\nShutting down...")
        finally:
            self.running = False
            audio_input.stop()


def load_settings() -> dict:
    """Load settings from config file."""
    config_path = PROJECT_DIR / "config" / "settings.json"

    if not config_path.exists():
        logger.warning(f"Settings file not found: {config_path}")
        return {}

    with open(config_path, "r") as f:
        return json.load(f)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="DODI - Didactic Doing Robot Companion"
    )
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug mode - show all transcriptions"
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
    print("[1/3] Loading settings...")
    settings = load_settings()
    if not settings:
        print("  [-] Could not load settings")
        return 1
    print("  [+] Settings loaded")

    # Create DODI instance
    print("[2/3] Initializing components...")
    try:
        dodi = DODI(settings, debug=args.debug)
        print("  [+] Components initialized")
    except Exception as e:
        print(f"  [-] Initialization failed: {e}")
        logger.exception("Initialization error")
        return 1

    # Run main loop
    print("[3/3] Starting DODI...")
    try:
        dodi.run()
    except Exception as e:
        print(f"Error: {e}")
        logger.exception("Runtime error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
