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
import asyncio
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
from tts import PiperTTS, StreamingSpeechManager
from ai.factory import create_provider
from streaming.factory import create_streaming_provider, is_provider_available

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

        stop_words_config = settings.get("stop_words", {})
        if isinstance(stop_words_config, list):
            # Backwards compatibility with old array format
            self.stop_words_enabled = True
            self.stop_words = [w.lower() for w in stop_words_config]
        else:
            self.stop_words_enabled = stop_words_config.get("enabled", True)
            self.stop_words = [w.lower() for w in stop_words_config.get("words", ["stop"])]

        # Audio settings
        audio_config = settings.get("audio", {})
        self.sample_rate = audio_config.get("sample_rate", 16000)
        self.device_name = audio_config.get("input_device")

        # Streaming config
        streaming_config = settings.get("streaming", {})
        self.use_streaming = streaming_config.get("enabled", True)
        self.streaming_provider_name = streaming_config.get("active", "gemini")
        self.streaming_provider_config = streaming_config.get(self.streaming_provider_name, {})
        self.min_sentence_length = streaming_config.get("min_sentence_length", 10)

        # Initialize components (lazy loading)
        self._audio_input: Optional[AudioInput] = None
        self._audio_output: Optional[AudioOutput] = None
        self._stt: Optional[VoskSTT] = None
        self._tts: Optional[PiperTTS] = None
        self._ai_provider = None
        self._persona: str = ""
        self._streaming_speech: Optional[StreamingSpeechManager] = None
        self._voice_streaming_provider = None

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

        # Listen for stop words while speaking (if enabled)
        stt.reset()
        while tts.is_speaking(aplay_proc):
            if self.stop_words_enabled:
                audio_data = audio_input.read(timeout=0.1)
                if audio_data:
                    result = stt.process_audio(audio_data)
                    if result and self.contains_stop_word(result):
                        logger.info(f"Stop word detected: {result}")
                        tts.stop_speaking(piper_proc, aplay_proc)
                        return False  # Interrupted
            else:
                time.sleep(0.1)

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

    def _init_streaming_speech(self) -> StreamingSpeechManager:
        """Initialize streaming speech manager."""
        if self._streaming_speech is None:
            self._streaming_speech = StreamingSpeechManager(
                tts=self._init_tts(),
                stt=self._init_stt(),
                audio_input=self._init_audio_input(),
                stop_words=self.stop_words,
                debug=self.debug
            )
        return self._streaming_speech

    def _init_voice_streaming_provider(self):
        """Initialize voice streaming provider."""
        if self._voice_streaming_provider is None:
            try:
                self._voice_streaming_provider = create_streaming_provider(
                    self.streaming_provider_name,
                    self.streaming_provider_config,
                    input_sample_rate=self.sample_rate
                )
                logger.info(f"Voice streaming provider initialized: {self.streaming_provider_name}")
            except Exception as e:
                logger.error(f"Failed to initialize voice streaming: {e}")
                self._voice_streaming_provider = None
        return self._voice_streaming_provider

    def _can_use_voice_streaming(self) -> bool:
        """Check if voice streaming is available."""
        return (
            self.use_streaming and
            is_provider_available(self.streaming_provider_name)
        )

    async def _run_voice_session(self) -> bool:
        """
        Run a voice streaming session after wake word.

        Returns:
            True if completed successfully, False otherwise
        """
        provider = self._init_voice_streaming_provider()
        if provider is None:
            return False

        audio_input = self._init_audio_input()
        stt = self._init_stt()
        persona = self.load_persona()

        session_complete = asyncio.Event()
        interrupted = False

        def on_end():
            session_complete.set()

        try:
            # Start voice session
            await provider.start_session(
                system_prompt=persona,
                on_end=on_end
            )

            logger.info("Voice streaming session started")
            if self.debug:
                print("[DEBUG] Voice streaming active - speak now...")

            # Stream audio to the provider until response is complete
            start_time = time.time()
            silence_start = None
            max_session_time = 30.0  # Maximum session duration

            while not session_complete.is_set():
                # Check for timeout
                if time.time() - start_time > max_session_time:
                    logger.info("Session timeout")
                    break

                # Read audio from microphone
                audio_data = audio_input.read(timeout=0.05)
                if audio_data:
                    # Check for stop words (if enabled) - always process, even while AI is speaking
                    if self.stop_words_enabled:
                        result = stt.process_audio(audio_data)
                        if result and self.contains_stop_word(result):
                            logger.info(f"Stop word detected: {result}")
                            await provider.interrupt()
                            interrupted = True
                            break

                    # Skip sending audio to provider while AI is speaking (avoid echo feedback)
                    if provider.is_speaking:
                        continue

                    # Send audio to provider
                    await provider.send_audio(audio_data)
                    silence_start = time.time()
                else:
                    # Check for silence (end of user speech)
                    if silence_start and time.time() - silence_start > 1.5:
                        # User has stopped speaking, commit audio
                        if hasattr(provider, 'commit_audio'):
                            await provider.commit_audio()
                        silence_start = None

                # Small delay to prevent busy-waiting
                await asyncio.sleep(0.01)

            return not interrupted

        except Exception as e:
            logger.error(f"Voice session error: {e}")
            if self.debug:
                print(f"[DEBUG] Voice session error: {e}")
            return False

        finally:
            try:
                await provider.stop_session()
            except Exception:
                pass
            stt.reset()

    def _handle_wake_activation(self, after_wake: str = "") -> None:
        """
        Handle wake word activation - start voice streaming or text processing.

        Args:
            after_wake: Optional text detected after the wake word (for text mode)
        """
        if self._can_use_voice_streaming():
            self.process_voice_streaming()
        else:
            # Text-based processing fallback
            if after_wake:
                command = after_wake
            else:
                command = self.listen_for_command(timeout=5.0)

            if command and not self.contains_stop_word(command):
                if self.use_streaming:
                    self.process_command_streaming(command)
                else:
                    response = self.process_command(command)
                    self.speak(response)

    def process_voice_streaming(self) -> bool:
        """
        Process a voice conversation using voice streaming.

        Returns:
            True if completed, False if interrupted or failed
        """
        logger.info(f"Starting voice streaming with {self.streaming_provider_name}")
        if self.debug:
            print(f"[DEBUG] Voice streaming provider: {self.streaming_provider_name}")

        try:
            return asyncio.run(self._run_voice_session())
        except Exception as e:
            logger.error(f"Voice streaming failed: {e}")
            if self.debug:
                print(f"[DEBUG] Voice streaming failed: {e}")
            return False

    def process_command_streaming(self, command: str) -> bool:
        """
        Process command with streaming response and speech.

        Args:
            command: User command text

        Returns:
            True if completed, False if interrupted
        """
        if not command.strip():
            self.speak("I didn't catch that. Could you say it again?")
            return True

        logger.info(f"Processing command (streaming): {command}")
        ai = self._init_ai_provider()
        persona = self.load_persona()
        streaming_speech = self._init_streaming_speech()

        try:
            if self.debug:
                print(f"[DEBUG] Sending to AI (streaming): {command}")

            # Get streaming response from AI
            text_stream = ai.chat_stream(command, system_prompt=persona)

            # Stream and speak
            return streaming_speech.stream_and_speak(text_stream)

        except Exception as e:
            logger.error(f"AI streaming error: {e}")
            if self.debug:
                print(f"[DEBUG] AI streaming error: {e}")
            # Fall back to non-streaming
            try:
                response = self.process_command(command)
                return self.speak(response)
            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")
                self.speak("Sorry, I'm having trouble right now.")
                return False

    def run(self) -> None:
        """Main conversation loop."""
        print()
        print("=" * 50)
        print("  DODI is listening...")
        print(f"  Wake words: {', '.join(self.wake_words)}")
        if self.stop_words_enabled:
            print(f"  Stop words: {', '.join(self.stop_words)}")
        else:
            print("  Stop words: disabled")
        if self.use_streaming:
            if self._can_use_voice_streaming():
                print(f"  Voice streaming: {self.streaming_provider_name}")
            else:
                print("  Text streaming: enabled (voice API key not set)")
        else:
            print("  Streaming: disabled")
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
                    # Check partial for immediate wake word activation
                    partial = stt.get_partial()
                    if partial:
                        if self.debug:
                            print(f"[DEBUG] Partial: {partial}")
                        has_wake, _ = self.contains_wake_word(partial)
                        if has_wake:
                            logger.info(f"Wake word detected in partial: {partial}")
                            stt.reset()
                            self._handle_wake_activation()
                            continue

                if result:
                    if self.debug:
                        print(f"[DEBUG] Transcribed: {result}")
                    has_wake, after_wake = self.contains_wake_word(result)
                    if has_wake:
                        logger.info(f"Wake word detected in: {result}")
                        if self.debug:
                            print(f"[DEBUG] Wake word found! After wake: '{after_wake}'")
                        stt.reset()
                        self._handle_wake_activation(after_wake)

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
