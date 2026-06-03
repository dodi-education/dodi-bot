"""Gemini Live API voice streaming provider."""

import asyncio
import logging
from typing import Optional, Callable

from .provider_base import VoiceStreamingProvider
from audio import AudioOutput

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
        self._audio_output: Optional[AudioOutput] = None

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
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    silence_duration_ms=1000  # Require 1 second of silence before end
                )
            ),
            # Transcribe both sides. We use these as conversation activity
            # signals (to drive the inactivity timeout) and for debug logging.
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
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

        if self._audio_output is not None:
            # Drop any queued audio and close the stream (immediate stop, e.g.
            # when the user double-taps to return to DEAF).
            self._audio_output.stop_stream()
            self._audio_output = None

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

            # Current Live API: stream realtime audio via send_realtime_input.
            # The old send(input=LiveClientRealtimeInput(media_chunks=...)) path
            # is deprecated and now rejected with a 1007 protocol error.
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type=f"audio/pcm;rate={self.sample_rate}"
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

        # Open the streaming audio output
        self._start_audio_output()

    # Gemini Live streams 24kHz mono 16-bit PCM.
    OUTPUT_SAMPLE_RATE = 24000

    def _start_audio_output(self) -> None:
        """Open the streaming audio output (sounddevice, default device).

        Uses the same hardware path as the night-mode alarm, instead of a
        hardcoded aplay device which pointed at the wrong sound card.
        """
        self._audio_output = AudioOutput(
            sample_rate=self.OUTPUT_SAMPLE_RATE, volume=self.volume
        )
        self._audio_output.start_stream(self.OUTPUT_SAMPLE_RATE)

    async def _receive_loop(self) -> None:
        """Background task to continuously receive and process responses.

        Gemini's ``session.receive()`` generator ends after each
        ``turn_complete``. For a fluent, native conversation we re-enter it in a
        loop so the session stays continuous across turns — no per-turn teardown
        — until the connection drops or we disconnect.
        """
        try:
            while self._connected:
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
                                # AI is speaking (volume is applied by AudioOutput)
                                self._set_speaking(True)
                                if self._audio_output is not None:
                                    self._audio_output.feed_stream(audio_data)
                                if self._audio_callback:
                                    self._audio_callback(audio_data)

                        # Check for text content
                        if hasattr(part, 'text') and part.text:
                            if self._transcript_callback:
                                self._transcript_callback(part.text)

                # Input (user) / output (AI) transcriptions: surface as activity
                # signals + debug text. Routed through the same transcript callback.
                in_tx = getattr(server_content, 'input_transcription', None)
                if in_tx and in_tx.text and self._transcript_callback:
                    self._transcript_callback(in_tx.text)
                out_tx = getattr(server_content, 'output_transcription', None)
                if out_tx and out_tx.text and self._transcript_callback:
                    self._transcript_callback(out_tx.text)

                # User barge-in: the model's in-progress audio was interrupted.
                if getattr(server_content, 'interrupted', None):
                    self._set_speaking(False)

                # Turn complete: the AI finished its turn. In the continuous
                # model this is NOT end-of-session — the receive loop keeps
                # running. The callback is only an "AI turn finished" signal.
                if hasattr(server_content, 'turn_complete') and server_content.turn_complete:
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

        # Drop queued audio and reopen a fresh stream.
        if self._audio_output is not None:
            self._audio_output.stop_stream()
            self._audio_output.start_stream(self.OUTPUT_SAMPLE_RATE)

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

            # Current Live API: turn-based text goes through send_client_content.
            # The old send(input=LiveClientContent(...)) path is deprecated.
            await self._session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=text)]
                ),
                turn_complete=True
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
        handles turn detection automatically. Mixing turn-based
        send_client_content into a send_realtime_input audio stream is
        discouraged by the API, so this method is a no-op.
        """
        # No-op: Gemini's automatic VAD handles turn detection. Interleaving
        # send_client_content(turn_complete=True) with the realtime audio
        # stream is not recommended and can break turn-taking.
        logger.debug("commit_audio called (no-op with automatic VAD)")
