"""OpenAI Realtime API voice streaming provider."""

import asyncio
import base64
import json
import logging
import subprocess
from typing import Optional, Callable

try:
    import websockets
except ImportError:
    websockets = None

from .provider_base import VoiceStreamingProvider

logger = logging.getLogger(__name__)

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


class OpenAIRealtimeProvider(VoiceStreamingProvider):
    """
    OpenAI Realtime API provider for voice-to-voice streaming.

    Uses WebSocket for bidirectional audio streaming with GPT-4o.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-realtime-preview",
        voice: str = "alloy",
        sample_rate: int = 24000,
        base_url: str = None,
        input_sample_rate: int = None
    ):
        """
        Initialize OpenAI Realtime provider.

        Args:
            api_key: OpenAI API key
            model: Model to use (default: gpt-4o-realtime-preview)
            voice: Voice for responses (alloy, echo, fable, onyx, nova, shimmer)
            sample_rate: Audio sample rate (24000 for OpenAI Realtime)
            base_url: Optional custom WebSocket URL (for Grok compatibility)
            input_sample_rate: Sample rate of input audio (will resample if different from sample_rate)
        """
        super().__init__(api_key, model, voice, sample_rate)
        self.base_url = base_url or OPENAI_REALTIME_URL
        self.input_sample_rate = input_sample_rate or sample_rate
        self._ws = None
        self._receive_task = None
        self._audio_callback = None
        self._transcript_callback = None
        self._end_callback = None
        self._aplay_proc = None
        self._audio_chunks_sent = 0

    async def connect(self, system_prompt: Optional[str] = None) -> None:
        """Connect to OpenAI Realtime API."""
        if websockets is None:
            raise ImportError("websockets package required. Install with: pip install websockets")

        url = f"{self.base_url}?model={self.model}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1"
        }

        logger.info(f"Connecting to {self.base_url}...")
        self._ws = await websockets.connect(url, additional_headers=headers)
        self._connected = True
        logger.info("Connected to OpenAI Realtime API")

        # Configure session
        await self._configure_session(system_prompt)

    async def _configure_session(self, system_prompt: Optional[str] = None) -> None:
        """Configure the realtime session."""
        config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": self.voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                }
            }
        }

        if system_prompt:
            config["session"]["instructions"] = system_prompt

        await self._ws.send(json.dumps(config))
        logger.info("Session configured")

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

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False
        logger.info("Disconnected from OpenAI Realtime API")

    def _resample_audio(self, audio_data: bytes, from_rate: int, to_rate: int) -> bytes:
        """Resample PCM16 audio from one sample rate to another using linear interpolation."""
        import struct

        # Decode PCM16 samples (16-bit signed little-endian)
        num_samples = len(audio_data) // 2
        samples = struct.unpack(f'<{num_samples}h', audio_data)

        # Calculate resampling ratio
        ratio = to_rate / from_rate
        new_length = int(num_samples * ratio)
        resampled = []

        for i in range(new_length):
            src_idx = i / ratio
            idx = int(src_idx)
            frac = src_idx - idx

            if idx + 1 < num_samples:
                # Linear interpolation between adjacent samples
                sample = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
            else:
                sample = samples[-1] if samples else 0
            resampled.append(max(-32768, min(32767, sample)))

        return struct.pack(f'<{len(resampled)}h', *resampled)

    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio chunk to the API."""
        if not self._connected or not self._ws:
            return

        # Resample if input sample rate differs from API sample rate (24kHz)
        if self.input_sample_rate != self.sample_rate:
            audio_data = self._resample_audio(audio_data, self.input_sample_rate, self.sample_rate)

        # OpenAI expects base64-encoded PCM16 audio
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        message = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        await self._ws.send(json.dumps(message))

        self._audio_chunks_sent += 1
        if self._audio_chunks_sent % 50 == 0:
            logger.info(f"Audio chunks sent: {self._audio_chunks_sent}")

    async def receive_audio(self) -> Optional[bytes]:
        """Receive audio from the API (handled by _receive_loop)."""
        # Audio is handled via callbacks in _receive_loop
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
        # 24kHz, 16-bit, mono PCM
        self._aplay_proc = subprocess.Popen(
            ["aplay", "-D", "plughw:1,0", "-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1"],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

    async def _receive_loop(self) -> None:
        """Background task to receive and process messages."""
        try:
            async for message in self._ws:
                await self._handle_message(json.loads(message))
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")

    async def _handle_message(self, msg: dict) -> None:
        """Handle incoming WebSocket message."""
        msg_type = msg.get("type", "")

        # Log all message types for debugging
        if msg_type not in ("response.audio.delta", "input_audio_buffer.speech_started",
                           "input_audio_buffer.speech_stopped", "input_audio_buffer.committed"):
            logger.info(f"WS message: {msg_type}")

        if msg_type == "response.audio.delta":
            # Audio chunk received - AI is speaking
            self._set_speaking(True)
            audio_b64 = msg.get("delta", "")
            if audio_b64:
                logger.info(f"Audio delta: {len(audio_b64)} chars")
                audio_data = base64.b64decode(audio_b64)
                if self._aplay_proc and self._aplay_proc.stdin:
                    try:
                        self._aplay_proc.stdin.write(audio_data)
                        self._aplay_proc.stdin.flush()
                    except BrokenPipeError:
                        pass
                if self._audio_callback:
                    self._audio_callback(audio_data)

        elif msg_type == "response.audio_transcript.delta":
            # Transcript chunk received
            text = msg.get("delta", "")
            logger.info(f"Transcript delta: {text}")
            if text and self._transcript_callback:
                self._transcript_callback(text)

        elif msg_type == "response.done":
            # Response complete - AI finished speaking
            self._set_speaking(False)
            response = msg.get("response", {})
            status = response.get("status", "unknown")
            output = response.get("output", [])
            status_details = response.get("status_details", {})
            logger.info(f"Response complete: status={status}, outputs={len(output)}")
            if status == "failed":
                logger.error(f"Response failed: {status_details}")
            if output:
                for i, item in enumerate(output):
                    item_type = item.get("type", "unknown")
                    logger.info(f"  Output[{i}]: type={item_type}")
            if self._end_callback:
                self._end_callback()

        elif msg_type == "error":
            error = msg.get("error", {})
            logger.error(f"API error: {error.get('message', 'Unknown error')} - {error}")

        elif msg_type == "session.created":
            logger.info("Session created")

        elif msg_type == "session.updated":
            logger.info("Session updated")

        elif msg_type == "input_audio_buffer.speech_started":
            logger.info("VAD: Speech started")

        elif msg_type == "input_audio_buffer.speech_stopped":
            logger.info("VAD: Speech stopped")

        elif msg_type == "conversation.item.input_audio_transcription.failed":
            error = msg.get("error", {})
            logger.error(f"Transcription failed: {error.get('message', 'Unknown')} - {error}")

        elif msg_type == "rate_limits.updated":
            pass  # Ignore rate limit updates

        elif "failed" in msg_type or "error" in msg_type:
            # Log any other failure/error messages
            logger.error(f"Failed event: {msg_type} - {msg}")

    async def stop_session(self) -> None:
        """Stop the current session."""
        await self.disconnect()

    async def interrupt(self) -> None:
        """Interrupt the current response."""
        # Clear speaking state
        self._is_speaking = False
        self._speaking_ended_at = None

        if not self._connected or not self._ws:
            return

        message = {"type": "response.cancel"}
        await self._ws.send(json.dumps(message))

        # Clear audio output
        if self._aplay_proc:
            self._aplay_proc.terminate()
            self._start_audio_output()

    async def commit_audio(self) -> None:
        """Signal end of audio input and trigger response."""
        if not self._connected or not self._ws:
            return

        logger.info(f"Committing audio buffer ({self._audio_chunks_sent} chunks sent)")

        # Commit the audio buffer
        await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        # Create response
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def send_text_prompt(self, text: str) -> None:
        """
        Send a text prompt to trigger AI response without audio input.

        This enables proactive speech from the AI (e.g., greetings).
        """
        if not self._connected or not self._ws:
            return

        logger.info(f"Sending text prompt: {text[:50]}...")

        # Create a conversation item with user text
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}]
            }
        }))

        # Trigger response generation
        await self._ws.send(json.dumps({"type": "response.create"}))
