"""Gemini Live API voice streaming provider."""

import asyncio
import logging
import subprocess
from typing import Optional, Callable

from .provider_base import VoiceStreamingProvider

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
        sample_rate: int = 16000
    ):
        """
        Initialize Gemini Live provider.

        Args:
            api_key: Google/Gemini API key
            model: Model to use (default: gemini-2.0-flash-live-001)
            voice: Voice for responses (Puck, Charon, Kore, Fenrir, Aoede)
            sample_rate: Audio sample rate (16000 for Gemini)
        """
        super().__init__(api_key, model, voice, sample_rate)
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
            pass
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")

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
