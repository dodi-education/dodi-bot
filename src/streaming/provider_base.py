"""Abstract base class for voice streaming providers."""

from abc import ABC, abstractmethod
from typing import Optional, Callable
import logging
import time

logger = logging.getLogger(__name__)


class VoiceStreamingProvider(ABC):
    """
    Base class for real-time voice streaming providers.

    Handles bidirectional audio streaming:
    - Send microphone audio to cloud AI
    - Receive and play audio response in real-time
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        sample_rate: int = 24000,
        volume: int = 100
    ):
        """
        Initialize the voice streaming provider.

        Args:
            api_key: API key for the provider
            model: Model identifier
            voice: Voice identifier for TTS
            sample_rate: Audio sample rate in Hz
            volume: Output volume percentage (0-100)
        """
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.sample_rate = sample_rate
        self.volume = volume
        self._connected = False
        self._is_speaking = False
        self._speaking_ended_at: Optional[float] = None
        self._echo_cooldown_seconds = 0.5

    @abstractmethod
    async def connect(self, system_prompt: Optional[str] = None) -> None:
        """
        Establish connection to the voice streaming API.

        Args:
            system_prompt: Optional system prompt (persona) to configure the AI
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection to the API."""
        pass

    @abstractmethod
    async def send_audio(self, audio_data: bytes) -> None:
        """
        Send audio data to the API.

        Args:
            audio_data: Raw PCM audio bytes
        """
        pass

    @abstractmethod
    async def receive_audio(self) -> Optional[bytes]:
        """
        Receive audio data from the API.

        Returns:
            Raw PCM audio bytes, or None if no data available
        """
        pass

    @abstractmethod
    async def start_session(
        self,
        system_prompt: Optional[str] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_end: Optional[Callable[[], None]] = None
    ) -> None:
        """
        Start a voice conversation session.

        Args:
            system_prompt: System prompt/persona for the AI
            on_audio: Callback for received audio chunks
            on_transcript: Callback for transcription text
            on_end: Callback when response is complete
        """
        pass

    @abstractmethod
    async def stop_session(self) -> None:
        """Stop the current voice session."""
        pass

    @abstractmethod
    async def interrupt(self) -> None:
        """Interrupt the current response (barge-in)."""
        pass

    @abstractmethod
    async def send_text_prompt(self, text: str) -> None:
        """
        Send a text prompt to trigger AI response without audio input.

        This enables proactive speech from the AI (e.g., greetings)
        without requiring the user to speak first.

        Args:
            text: The text prompt to send to the AI
        """
        pass

    @abstractmethod
    async def clear_input_buffer(self) -> None:
        """
        Clear any buffered audio input on the server side.

        This prevents echo/feedback from being interpreted as user speech.
        Should be called after AI finishes speaking before listening.
        """
        pass

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to the API."""
        return self._connected

    @property
    def is_speaking(self) -> bool:
        """
        Check if AI is currently speaking (outputting audio).

        Returns True if actively speaking, or within the cooldown period
        after speech ended (to handle echo tail from speakers).
        """
        if self._is_speaking:
            return True
        if self._speaking_ended_at is not None:
            if time.time() - self._speaking_ended_at < self._echo_cooldown_seconds:
                return True
        return False

    def _set_speaking(self, speaking: bool) -> None:
        """Update speaking state (for subclass use)."""
        if speaking:
            self._is_speaking = True
            self._speaking_ended_at = None
        else:
            self._is_speaking = False
            self._speaking_ended_at = time.time()

    def get_provider_name(self) -> str:
        """Return the provider name for logging."""
        return self.__class__.__name__.replace("Provider", "")

    @property
    def supports_session_update(self) -> bool:
        """
        Whether this provider can update instructions mid-session.

        Returns:
            True if provider supports session.update or equivalent
        """
        return False

    async def update_session_instructions(self, instructions: str) -> bool:
        """
        Update session instructions without reconnecting.

        For providers that support it (OpenAI/Grok), this updates the
        system instructions mid-session.

        Args:
            instructions: New system prompt/instructions

        Returns:
            True if update succeeded, False otherwise
        """
        return False

    async def prepare_for_next_turn(self) -> None:
        """
        Prepare the session for the next conversation turn.

        Called between turns to clear buffers and reset state while
        keeping the connection alive. Default implementation clears
        the input buffer.
        """
        await self.clear_input_buffer()

    async def send_keepalive(self) -> bool:
        """
        Send keepalive to prevent connection timeout.

        Override in subclasses that need periodic activity to maintain
        their connection (e.g., Gemini).

        Returns:
            True if keepalive succeeded or not needed, False if failed
        """
        return True  # Default: no-op, assume connection stays alive
