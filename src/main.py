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
import random
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
from conversation import ConversationSession, SessionState

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

        # Greeting words
        greeting_config = settings.get("greeting_words", {})
        if isinstance(greeting_config, list):
            # Backwards compatibility with array format
            self.greeting_words_enabled = True
            self.greeting_words = [w.lower() for w in greeting_config]
            self.greeting_prompt = "The user just greeted you. Greet them back as described in your instructions."
            self.greeting_prompt_with_query = "The user greets you and asks: {query}. Greet them briefly and answer their question."
        else:
            self.greeting_words_enabled = greeting_config.get("enabled", True)
            self.greeting_words = [w.lower() for w in greeting_config.get("words", ["hallo", "hi", "hey"])]
            self.greeting_prompt = greeting_config.get(
                "prompt_greeting",
                "The user just greeted you. Greet them back as described in your instructions."
            )
            self.greeting_prompt_with_query = greeting_config.get(
                "prompt_greeting_with_query",
                "The user greets you and asks: {query}. Greet them briefly and answer their question."
            )

        # Audio settings
        audio_config = settings.get("audio", {})
        self.sample_rate = audio_config.get("sample_rate", 16000)
        self.device_name = audio_config.get("input_device")
        self.volume = audio_config.get("volume", 100)

        # Streaming config
        streaming_config = settings.get("streaming", {})
        self.use_streaming = streaming_config.get("enabled", True)
        self.streaming_provider_name = streaming_config.get("active", "gemini")
        self.streaming_provider_config = streaming_config.get(self.streaming_provider_name, {})
        self.min_sentence_length = streaming_config.get("min_sentence_length", 10)

        # Conversation config
        conversation_config = settings.get("conversation", {})
        self.conversation_enabled = conversation_config.get("enabled", True)
        self.active_timeout = conversation_config.get("timeout_seconds", 10.0)
        self.max_conversation_turns = conversation_config.get("max_turns", 20)
        self.reset_phrases = [p.lower() for p in conversation_config.get(
            "reset_phrases", ["new topic", "start over", "neues thema", "vergiss das"]
        )]

        # Initialize components (lazy loading)
        self._audio_input: Optional[AudioInput] = None
        self._audio_output: Optional[AudioOutput] = None
        self._stt: Optional[VoskSTT] = None
        self._tts: Optional[PiperTTS] = None
        self._ai_provider = None
        self._persona: str = ""
        self._streaming_speech: Optional[StreamingSpeechManager] = None
        self._voice_streaming_provider = None
        self._session: Optional[ConversationSession] = None

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
            self._audio_output = AudioOutput(volume=self.volume)
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
            self._tts = PiperTTS(str(model_path), volume=self.volume)
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

    def contains_wake_word(self, text: str) -> tuple[bool, str, bool]:
        """
        Check if text contains a wake word and optionally a greeting.

        Returns:
            Tuple of (wake_word_found, text_after_wake_word, greeting_detected)
        """
        text_lower = text.lower()
        for wake_word in self.wake_words:
            if wake_word in text_lower:
                # Get text after the wake word
                idx = text_lower.find(wake_word)
                after_wake = text[idx + len(wake_word):].strip()
                # Check if greeting word was present in the full text
                greeting_detected = self.contains_greeting_word(text_lower)
                return True, after_wake, greeting_detected
        return False, "", False

    def contains_stop_word(self, text: str) -> bool:
        """Check if text contains a stop word."""
        text_lower = text.lower()
        return any(stop_word in text_lower for stop_word in self.stop_words)

    def contains_greeting_word(self, text: str) -> bool:
        """Check if text contains a greeting word."""
        if not self.greeting_words_enabled:
            return False
        text_lower = text.lower()
        return any(greeting in text_lower for greeting in self.greeting_words)

    def _is_reset_phrase(self, text: str) -> bool:
        """Check if text contains a conversation reset phrase."""
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in self.reset_phrases)

    def _init_session(self) -> ConversationSession:
        """Initialize or return conversation session."""
        if self._session is None:
            self._session = ConversationSession(
                timeout_seconds=self.active_timeout,
                max_turns=self.max_conversation_turns
            )
        return self._session

    def _clear_audio_for_listening(self, audio_input, stt, delay: float = 0.5) -> None:
        """
        Clear buffered audio and reset STT to prepare for fresh listening.

        This prevents echo/feedback from AI speech being interpreted as user input.
        Uses a double-clear pattern with delay to catch lingering audio.

        Args:
            audio_input: Audio input instance with clear_queue() method
            stt: STT instance with reset() method
            delay: Seconds to wait between clears for echo to settle
        """
        audio_input.clear_queue()
        stt.reset()
        time.sleep(delay)
        audio_input.clear_queue()

    def _build_context_prompt(self, session: ConversationSession) -> str:
        """Build system prompt with conversation history context."""
        persona = self.load_persona()

        if session.history.is_empty():
            return persona

        history_text = session.history.get_formatted_text(max_messages=6)
        return f"""{persona}

Previous conversation context:
{history_text}

Continue the conversation naturally, referencing previous context when relevant."""

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
                    input_sample_rate=self.sample_rate,
                    volume=self.volume
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

    def _listen_for_speech_active(self, timeout: float) -> str:
        """
        Listen for speech in active mode (no wake word required).

        Args:
            timeout: Max seconds to wait for speech

        Returns:
            Transcribed speech text, empty string if timeout
        """
        stt = self._init_stt()
        audio_input = self._init_audio_input()

        stt.reset()
        audio_input.clear_queue()

        start_time = time.time()
        silence_start = None
        speech_detected = False
        command_text = ""

        while time.time() - start_time < timeout:
            audio_data = audio_input.read(timeout=0.1)
            if audio_data is None:
                continue

            result = stt.process_audio(audio_data)
            if result:
                command_text = result
                speech_detected = True
                silence_start = time.time()
                if self.debug:
                    print(f"[DEBUG] Active mode heard: {result}")
            else:
                partial = stt.get_partial()
                if partial:
                    silence_start = time.time()
                    speech_detected = True
                    # Reset timeout when user starts speaking
                    start_time = time.time()
                elif speech_detected and silence_start and time.time() - silence_start > 1.0:
                    # 1 second of silence after speech - done
                    break

        if not command_text and speech_detected:
            command_text = stt.finalize()

        return command_text

    def _wait_for_followup_or_timeout(self, session: ConversationSession) -> bool:
        """
        Wait for user to start speaking or timeout expires.

        Returns:
            True if user started speaking, False if timeout
        """
        stt = self._init_stt()
        audio_input = self._init_audio_input()

        # Clear any buffered audio from during AI speech (including echo)
        self._clear_audio_for_listening(audio_input, stt)

        session.set_waiting()
        start_time = time.time()

        while time.time() - start_time < self.active_timeout:
            audio_data = audio_input.read(timeout=0.1)
            if audio_data:
                # Check for any speech activity
                result = stt.process_audio(audio_data)
                if result:
                    session.cancel_timeout()
                    return True
                partial = stt.get_partial()
                if partial:
                    session.cancel_timeout()
                    return True

        return False

    def _process_command_with_history(self, command: str, session: ConversationSession) -> str:
        """
        Send command to AI with conversation history.

        Args:
            command: User command text
            session: Conversation session with history

        Returns:
            AI response text
        """
        if not command.strip():
            return "I didn't catch that. Could you say it again?"

        logger.info(f"Processing command with history: {command}")
        ai = self._init_ai_provider()
        persona = self.load_persona()

        # Build messages with history
        messages = session.history.get_formatted_history(format="openai")
        messages.append({"role": "user", "content": command})

        try:
            if self.debug:
                print(f"[DEBUG] Sending to AI with {len(messages)} messages")
            response = ai.chat_with_history(messages, system_prompt=persona)
            logger.info(f"AI response: {response}")
            if self.debug:
                print(f"[DEBUG] AI response: {response}")
            return response
        except Exception as e:
            logger.error(f"AI error: {e}")
            if self.debug:
                print(f"[DEBUG] AI error: {e}")
            return "Sorry, I'm having trouble thinking right now."

    def _process_command_streaming_with_history(self, command: str, session: ConversationSession) -> tuple[bool, str]:
        """
        Process command with streaming response and speech, using history.

        Args:
            command: User command text
            session: Conversation session with history

        Returns:
            Tuple of (completed, response_text)
        """
        if not command.strip():
            self.speak("I didn't catch that. Could you say it again?")
            return True, ""

        logger.info(f"Processing command (streaming with history): {command}")
        ai = self._init_ai_provider()
        persona = self.load_persona()
        streaming_speech = self._init_streaming_speech()

        # Build messages with history
        messages = session.history.get_formatted_history(format="openai")
        messages.append({"role": "user", "content": command})

        try:
            if self.debug:
                print(f"[DEBUG] Sending to AI (streaming) with {len(messages)} messages")

            # Get streaming response from AI
            text_stream = ai.chat_stream_with_history(messages, system_prompt=persona)

            # Collect response while streaming
            response_parts = []

            def collecting_stream():
                for chunk in text_stream:
                    response_parts.append(chunk)
                    yield chunk

            # Stream and speak
            completed = streaming_speech.stream_and_speak(collecting_stream())
            return completed, "".join(response_parts)

        except Exception as e:
            logger.error(f"AI streaming error: {e}")
            if self.debug:
                print(f"[DEBUG] AI streaming error: {e}")
            # Fall back to non-streaming
            try:
                response = self._process_command_with_history(command, session)
                completed = self.speak(response)
                return completed, response
            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")
                self.speak("Sorry, I'm having trouble right now.")
                return False, ""

    def _run_active_mode_text(self, session: ConversationSession, initial_text: str = "") -> None:
        """
        Run active mode loop with text-based AI.

        Stays active until timeout or stop word, allowing multi-turn conversation.
        """
        logger.info("Entering active mode (text)")
        if self.debug:
            print("[DEBUG] Active mode started - listening for conversation...")

        while session.is_active():
            # Get user input
            session.set_listening()

            if initial_text:
                command = initial_text
                initial_text = ""
            else:
                command = self._listen_for_speech_active(timeout=self.active_timeout)

            if not command:
                # Timeout with no speech - return to passive
                logger.info("Active mode timeout - returning to passive")
                if self.debug:
                    print("[DEBUG] Timeout - no speech detected")
                break

            # Check for stop word
            if self.contains_stop_word(command):
                logger.info(f"Stop word detected: {command}")
                if self.debug:
                    print(f"[DEBUG] Stop word - returning to passive")
                break

            # Check for reset phrase
            if self._is_reset_phrase(command):
                session.reset_conversation()
                self.speak("Okay, let's start fresh!")
                if self.debug:
                    print("[DEBUG] Conversation reset")
                continue

            # Add user message to history
            session.history.add_user_message(command)
            session.set_processing()

            # Process with AI
            session.set_speaking()
            if self.use_streaming:
                completed, response = self._process_command_streaming_with_history(command, session)
            else:
                response = self._process_command_with_history(command, session)
                completed = self.speak(response)

            if response:
                session.history.add_assistant_message(response)

            if not completed:
                # Interrupted - return to passive
                logger.info("Response interrupted - returning to passive")
                break

            # After response, wait for follow-up
            if self.debug:
                print(f"[DEBUG] Waiting for follow-up (timeout: {self.active_timeout}s)...")

            if not self._wait_for_followup_or_timeout(session):
                logger.info("No follow-up - returning to passive")
                if self.debug:
                    print("[DEBUG] No follow-up detected - returning to passive")
                break

            # User is speaking - loop continues
            if self.debug:
                print("[DEBUG] Follow-up detected - continuing conversation")

    def _run_active_mode_voice(
        self,
        session: ConversationSession,
        initial_text: str = "",
        greeting_prompt: str = ""
    ) -> None:
        """
        Run active mode loop with voice streaming.

        Stays active until timeout or stop word, allowing multi-turn conversation.

        Args:
            session: Conversation session
            initial_text: Optional text detected after wake word
            greeting_prompt: Optional prompt to trigger AI speech first (for greetings)
        """
        logger.info("Entering active mode (voice streaming)")
        if self.debug:
            print("[DEBUG] Active mode started (voice streaming)...")

        while session.is_active():
            session.set_listening()

            # On first turn, pass greeting prompt to trigger AI speech first
            prompt_for_turn = greeting_prompt
            greeting_prompt = ""  # Clear after first use

            # Run one voice turn with conversation context
            completed, user_text, ai_text = asyncio.run(
                self._run_voice_session_with_context(session, initial_prompt=prompt_for_turn)
            )

            if not completed:
                # Interrupted or failed
                logger.info("Voice session interrupted - returning to passive")
                break

            # Store transcripts in history
            if user_text:
                session.history.add_user_message(user_text)
            if ai_text:
                session.history.add_assistant_message(ai_text)

            # Wait for follow-up or timeout
            if self.debug:
                print(f"[DEBUG] Waiting for follow-up (timeout: {self.active_timeout}s)...")

            if not self._wait_for_followup_or_timeout(session):
                logger.info("No follow-up - returning to passive")
                if self.debug:
                    print("[DEBUG] No follow-up detected - returning to passive")
                break

            # User is speaking - loop continues
            if self.debug:
                print("[DEBUG] Follow-up detected - continuing conversation")

    async def _run_voice_session_with_context(
        self,
        session: ConversationSession,
        initial_prompt: str = ""
    ) -> tuple[bool, str, str]:
        """
        Run a voice streaming session with conversation context.

        Args:
            session: Conversation session with history
            initial_prompt: Optional prompt to send first (triggers AI speech before listening)

        Returns:
            Tuple of (completed, user_transcript, ai_transcript)
        """
        provider = self._init_voice_streaming_provider()
        if provider is None:
            return False, "", ""

        audio_input = self._init_audio_input()
        stt = self._init_stt()

        # Build context prompt
        context_prompt = self._build_context_prompt(session)

        session_complete = asyncio.Event()
        interrupted = False
        user_transcript = ""
        ai_transcript = ""

        def on_end():
            session_complete.set()

        def on_transcript(text: str):
            nonlocal ai_transcript
            ai_transcript += text

        try:
            session.set_processing()

            # Start voice session with context
            await provider.start_session(
                system_prompt=context_prompt,
                on_end=on_end,
                on_transcript=on_transcript
            )

            logger.info("Voice streaming session started with context")

            # If initial prompt provided (e.g., greeting), send it to trigger AI speech first
            if initial_prompt:
                if self.debug:
                    print("[DEBUG] Sending initial prompt to trigger AI speech...")
                await provider.send_text_prompt(initial_prompt)
            else:
                if self.debug:
                    print("[DEBUG] Voice streaming active - speak now...")

            session.set_speaking()

            # Stream audio to the provider until response is complete
            start_time = time.time()
            silence_start = None
            max_session_time = 30.0

            while not session_complete.is_set():
                if time.time() - start_time > max_session_time:
                    logger.info("Session timeout")
                    break

                audio_data = audio_input.read(timeout=0.05)
                if audio_data:
                    # Check for stop words
                    if self.stop_words_enabled:
                        result = stt.process_audio(audio_data)
                        if result:
                            # Capture user transcript
                            user_transcript = result
                            if self.contains_stop_word(result):
                                logger.info(f"Stop word detected: {result}")
                                await provider.interrupt()
                                interrupted = True
                                break

                    if provider.is_speaking:
                        continue

                    await provider.send_audio(audio_data)
                    silence_start = time.time()
                else:
                    if silence_start and time.time() - silence_start > 1.5:
                        if hasattr(provider, 'commit_audio'):
                            await provider.commit_audio()
                        silence_start = None

                await asyncio.sleep(0.01)

            return not interrupted, user_transcript, ai_transcript

        except Exception as e:
            logger.error(f"Voice session error: {e}")
            if self.debug:
                print(f"[DEBUG] Voice session error: {e}")
            return False, "", ""

        finally:
            try:
                await provider.stop_session()
            except Exception:
                pass
            stt.reset()

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

    def _handle_wake_activation(self, after_wake: str = "", greeting_detected: bool = False) -> None:
        """
        Handle wake word activation - enter active mode for conversation.

        Args:
            after_wake: Optional text detected after the wake word
            greeting_detected: Whether a greeting word was detected with the wake word
        """
        session = self._init_session()

        # Check for conversation reset phrase
        if after_wake and self._is_reset_phrase(after_wake):
            session.reset_conversation()
            self.speak("Okay, let's start fresh!")
            return

        session.activate()

        try:
            # If greeting detected and this is a fresh session, trigger proactive greeting
            if greeting_detected and session.history.is_empty():
                self._handle_proactive_greeting(session, after_wake)
            elif self.conversation_enabled:
                # New multi-turn conversation mode
                if self._can_use_voice_streaming():
                    self._run_active_mode_voice(session, initial_text=after_wake)
                else:
                    self._run_active_mode_text(session, initial_text=after_wake)
            else:
                # Legacy single-turn mode
                if self._can_use_voice_streaming():
                    self.process_voice_streaming()
                else:
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
        finally:
            session.deactivate()

    def _handle_proactive_greeting(self, session: ConversationSession, after_wake: str = "") -> None:
        """
        Handle proactive greeting when user greets DODI.

        The AI generates a greeting based on persona instructions, speaks it,
        then continues with normal active mode for follow-up conversation.

        Args:
            session: Active conversation session
            after_wake: Optional text detected after the wake word (user's question)
        """
        logger.info("Greeting detected - initiating proactive greeting")
        if self.debug:
            print("[DEBUG] Proactive greeting triggered")

        # Build the greeting prompt from config
        if after_wake:
            # User greeted and asked something: "Hallo Dodi, wie ist das Wetter?"
            greeting_prompt = self.greeting_prompt_with_query.format(query=after_wake)
        else:
            # User just greeted: "Hallo Dodi"
            greeting_prompt = self.greeting_prompt

        # Use cloud voice streaming if available (preferred for natural voice)
        if self._can_use_voice_streaming():
            if self.debug:
                print("[DEBUG] Using cloud voice for greeting via active mode")
            # Pass greeting prompt to active mode - it handles everything
            self._run_active_mode_voice(session, greeting_prompt=greeting_prompt)
            return

        # Fallback to text-based greeting with local TTS
        session.set_processing()
        session.set_speaking()

        if self.use_streaming:
            completed, response = self._generate_greeting_streaming(session, greeting_prompt)
        else:
            response = self._generate_greeting(session, greeting_prompt)
            completed = self.speak(response)

        if response:
            session.history.add_assistant_message(response)

        if self.debug:
            print("[DEBUG] Greeting complete, entering conversation mode")

        # After greeting, wait for follow-up conversation
        if completed and self._wait_for_followup_or_timeout(session):
            # User is speaking - continue with normal active mode
            self._run_active_mode_text(session)

    def _generate_greeting(self, session: ConversationSession, prompt: str) -> str:
        """Generate a greeting response from the AI (non-streaming)."""
        ai = self._init_ai_provider()
        persona = self.load_persona()

        try:
            response = ai.chat(prompt, system_prompt=persona)
            logger.info(f"Greeting response: {response}")
            return response
        except Exception as e:
            logger.error(f"Greeting generation error: {e}")
            # Fallback to random greeting word from config
            fallback = random.choice(self.greeting_words).capitalize() + "!"
            return fallback

    def _generate_greeting_streaming(self, session: ConversationSession, prompt: str) -> tuple[bool, str]:
        """Generate and speak a greeting response (streaming)."""
        ai = self._init_ai_provider()
        persona = self.load_persona()
        streaming_speech = self._init_streaming_speech()

        try:
            text_stream = ai.chat_stream(prompt, system_prompt=persona)

            response_parts = []

            def collecting_stream():
                for chunk in text_stream:
                    response_parts.append(chunk)
                    yield chunk

            completed = streaming_speech.stream_and_speak(collecting_stream())
            return completed, "".join(response_parts)

        except Exception as e:
            logger.error(f"Greeting streaming error: {e}")
            # Fallback to random greeting word from config
            fallback = random.choice(self.greeting_words).capitalize() + "!"
            self.speak(fallback)
            return True, fallback

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
        if self.greeting_words_enabled:
            print(f"  Greeting words: {', '.join(self.greeting_words[:5])}...")
        else:
            print("  Greeting detection: disabled")
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
                        has_wake, _, greeting = self.contains_wake_word(partial)
                        if has_wake:
                            logger.info(f"Wake word detected in partial: {partial}")
                            stt.reset()
                            self._handle_wake_activation(greeting_detected=greeting)
                            continue

                if result:
                    if self.debug:
                        print(f"[DEBUG] Transcribed: {result}")
                    has_wake, after_wake, greeting = self.contains_wake_word(result)
                    if has_wake:
                        logger.info(f"Wake word detected in: {result}")
                        if self.debug:
                            print(f"[DEBUG] Wake word found! After wake: '{after_wake}', Greeting: {greeting}")
                        stt.reset()
                        self._handle_wake_activation(after_wake, greeting_detected=greeting)

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
