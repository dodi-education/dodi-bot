"""Gemini Live API voice streaming provider."""

import asyncio
import logging
import subprocess
from typing import Optional, Callable

from .provider_base import VoiceStreamingProvider
from audio.volume import scale_volume

logger = logging.getLogger(__name__)


class GeminiLiveProvider(VoiceStreamingProvider):
    """
    Google Gemini Live API provider for voice-to-voice streaming.

    Uses the google-genai library with Live API support.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash-native-audio",
        voice: str = "Puck",
        sample_rate: int = 16000,
        volume: int = 100
    ):
        """
        Initialize Gemini Live provider.

        Args:
            api_key: Google/Gemini API key
            model: Model to use (default: gemini-2.0-flash-live-001)
            voice: Voice for responses (Puck, Charon, Kore, Fenrir, Aoede)
            sample_rate: Audio sample rate (16000 for Gemini)
            volume: Output volume percentage (0-100)
        """
        super().__init__(api_key, model, voice, sample_rate, volume)
        self._client = None
        self._session = None
        self._session_context = None
        self._receive_task = None
        self._audio_callback = None
        self._transcript_callback = None
        self._end_callback = None
        self._aplay_proc = None

    async def connect(self, system_prompt: Optional[str] = None) -> None:
        """Connect to Gemini Live API."""
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ImportError("google-genai package required. Install with: pip install google-genai")

        logger.info(f"Connecting to Gemini Live API with model {self.model}...")

        self._client = genai.Client(api_key=self.api_key)

        # Configure Live API session
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice
                    )
                )
            ),
            # Enable automatic voice activity detection for turn-taking
            # This lets Gemini detect when the user stops speaking
            # Use LOW end sensitivity to avoid cutting off mid-sentence
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    startOfSpeechSensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    endOfSpeechSensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    silenceDurationMs=1000  # Require 1 second of silence before end
                )
            )
        )

        if system_prompt:
            config.system_instruction = types.Content(
                parts=[types.Part(text=system_prompt)]
            )

        # Connect and enter the async context
        self._session_context = self._client.aio.live.connect(
            model=self.model,
            config=config
        )
        self._session = await self._session_context.__aenter__()

        self._connected = True
        logger.info("Connected to Gemini Live API")

    async def disconnect(self) -> None:
        """Disconnect from the API."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._aplay_proc:
            # Close stdin to signal end of audio, then wait for playback to finish
            if self._aplay_proc.stdin:
                try:
                    self._aplay_proc.stdin.close()
                except Exception:
                    pass
            # Wait for aplay to finish playing buffered audio (max 5 seconds)
            try:
                self._aplay_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._aplay_proc.terminate()
            self._aplay_proc = None

        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_context = None
            self._session = None

        self._connected = False
        logger.info("Disconnected from Gemini Live API")

    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio chunk to the API."""
        if not self._connected or not self._session:
            return

        try:
            from google.genai import types

            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            data=audio_data,
                            mime_type=f"audio/pcm;rate={self.sample_rate}"
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error(f"Error sending audio: {e}")

    async def receive_audio(self) -> Optional[bytes]:
        """Receive audio from the API (handled by _receive_loop)."""
        return None

    async def start_session(
        self,
        system_prompt: Optional[str] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_end: Optional[Callable[[], None]] = None
    ) -> None:
        """Start a voice conversation session."""
        self._audio_callback = on_audio
        self._transcript_callback = on_transcript
        self._end_callback = on_end

        # Reset speaking state
        self._is_speaking = False
        self._speaking_ended_at = None

        if not self._connected:
            await self.connect(system_prompt)

        # Start receiving messages
        self._receive_task = asyncio.create_task(self._receive_loop())

        # Start aplay process for audio output
        self._start_audio_output()

    def _start_audio_output(self) -> None:
        """Start aplay process for audio playback."""
        # Gemini uses 24kHz output by default
        self._aplay_proc = subprocess.Popen(
            ["aplay", "-D", "plughw:1,0", "-f", "S16_LE", "-r", "24000", "-c", "1"],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

    async def _receive_loop(self) -> None:
        """Background task to receive and process responses."""
        try:
            async for response in self._session.receive():
                await self._handle_response(response)

        except asyncio.CancelledError:
            # Normal cancellation during disconnect
            pass
        except Exception as e:
            # Mark connection as dead so callers know to reconnect
            self._connected = False
            self._session = None
            logger.error(f"Error in receive loop: {e}")

            # Call end callback to unblock session_complete waits
            # This prevents the main loop from waiting forever
            if self._end_callback:
                try:
                    self._end_callback()
                except Exception as cb_error:
                    logger.error(f"Error in end callback: {cb_error}")

    async def _handle_response(self, response) -> None:
        """Handle incoming response from Gemini."""
        try:
            # Check for server content with audio
            if hasattr(response, 'server_content') and response.server_content:
                server_content = response.server_content

                # Check for model turn with audio parts
                if hasattr(server_content, 'model_turn') and server_content.model_turn:
                    for part in server_content.model_turn.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            audio_data = part.inline_data.data
                            if audio_data:
                                # AI is speaking
                                self._set_speaking(True)
                                # Apply volume scaling
                                audio_data = scale_volume(audio_data, self.volume)
                                if self._aplay_proc and self._aplay_proc.stdin:
                                    try:
                                        self._aplay_proc.stdin.write(audio_data)
                                        self._aplay_proc.stdin.flush()
                                    except BrokenPipeError:
                                        pass
                                if self._audio_callback:
                                    self._audio_callback(audio_data)

                        # Check for text content
                        if hasattr(part, 'text') and part.text:
                            if self._transcript_callback:
                                self._transcript_callback(part.text)

                # Check for turn complete
                if hasattr(server_content, 'turn_complete') and server_content.turn_complete:
                    # AI finished speaking
                    self._set_speaking(False)
                    logger.info("Turn complete")
                    if self._end_callback:
                        self._end_callback()

        except Exception as e:
            logger.error(f"Error handling response: {e}")

    async def stop_session(self) -> None:
        """Stop the current session."""
        await self.disconnect()

    async def interrupt(self) -> None:
        """Interrupt the current response."""
        # Clear speaking state
        self._is_speaking = False
        self._speaking_ended_at = None

        # Clear audio output
        if self._aplay_proc:
            self._aplay_proc.terminate()
            self._start_audio_output()

        logger.info("Interrupt requested")

    async def send_text_prompt(self, text: str) -> None:
        """
        Send a text prompt to trigger AI response without audio input.

        This enables proactive speech from the AI (e.g., greetings).
        """
        if not self._connected or not self._session:
            return

        logger.info(f"Sending text prompt: {text[:50]}...")

        try:
            from google.genai import types

            await self._session.send(
                input=types.LiveClientContent(
                    turns=[types.Content(
                        role="user",
                        parts=[types.Part(text=text)]
                    )],
                    turn_complete=True
                )
            )
        except Exception as e:
            logger.error(f"Error sending text prompt: {e}")

    async def clear_input_buffer(self) -> None:
        """Clear any buffered audio input (no-op for Gemini)."""
        # Gemini Live API doesn't have a buffer clear mechanism like OpenAI
        # The local audio queue clearing should be sufficient
        pass

    async def prepare_for_next_turn(self) -> None:
        """
        Prepare Gemini session for next turn.

        The Gemini receive() generator ends after each turn_complete,
        so we need to restart the receive loop for the next turn.
        """
        self._is_speaking = False
        self._speaking_ended_at = None

        # Restart receive loop if it completed (normal after turn_complete)
        if self._receive_task is not None and self._receive_task.done():
            # Check if it ended with an error
            try:
                self._receive_task.result()
            except Exception as e:
                logger.warning(f"Previous receive task ended with error: {e}")

            # Start new receive loop for next turn
            logger.debug("Restarting receive loop for next turn")
            self._receive_task = asyncio.create_task(self._receive_loop())

    async def send_keepalive(self) -> bool:
        """
        Check if the Gemini connection is still alive.

        Note: We don't send silent audio as it confuses Gemini's VAD.
        Just checks connection state - the _connected flag is set to False
        by _receive_loop() when actual errors occur.

        Returns:
            True if connection appears alive, False if dead
        """
        # Note: Don't check if receive_task.done() here - the receive task
        # completes normally after each turn (when Gemini sends turn_complete),
        # which doesn't mean the connection is dead.
        return self._connected and self._session is not None

    async def commit_audio(self) -> None:
        """
        Signal to Gemini that the user's audio input is complete.

        Note: With automatic_activity_detection enabled, Gemini's VAD
        handles turn detection automatically. Sending LiveClientContent
        after LiveClientRealtimeInput causes protocol errors (1007).
        This method is now a no-op.
        """
        # No-op: Gemini's automatic VAD handles turn detection
        # Sending LiveClientContent(turn_complete=True) after streaming
        # audio via LiveClientRealtimeInput causes error 1007
        logger.debug("commit_audio called (no-op with automatic VAD)")
