"""Abstract base class for voice streaming providers."""

from abc import ABC, abstractmethod
from typing import Optional, Callable
import logging

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
        sample_rate: int = 24000
    ):
        """
        Initialize the voice streaming provider.

        Args:
            api_key: API key for the provider
            model: Model identifier
            voice: Voice identifier for TTS
            sample_rate: Audio sample rate in Hz
        """
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.sample_rate = sample_rate
        self._connected = False

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

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to the API."""
        return self._connected

    def get_provider_name(self) -> str:
        """Return the provider name for logging."""
        return self.__class__.__name__.replace("Provider", "")
