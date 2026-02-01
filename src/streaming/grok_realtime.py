"""Grok Voice Agent API streaming provider."""

import logging
from .openai_realtime import OpenAIRealtimeProvider

logger = logging.getLogger(__name__)

GROK_REALTIME_URL = "wss://api.x.ai/v1/realtime"


class GrokRealtimeProvider(OpenAIRealtimeProvider):
    """
    Grok Voice Agent API provider.

    Uses xAI's OpenAI-compatible Realtime API for voice-to-voice streaming.
    Inherits from OpenAIRealtimeProvider since the API is compatible.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "grok-4-voice",
        voice: str = "default",
        sample_rate: int = 24000
    ):
        """
        Initialize Grok Realtime provider.

        Args:
            api_key: xAI/Grok API key
            model: Model to use (default: grok-2-voice)
            voice: Voice for responses
            sample_rate: Audio sample rate
        """
        super().__init__(
            api_key=api_key,
            model=model,
            voice=voice,
            sample_rate=sample_rate,
            base_url=GROK_REALTIME_URL
        )

    def get_provider_name(self) -> str:
        """Return the provider name for logging."""
        return "Grok"
