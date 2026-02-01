"""Google Gemini AI provider implementation."""

import logging
from typing import Iterator, Optional

from .provider_base import AIProviderBase

logger = logging.getLogger(__name__)


class GeminiProvider(AIProviderBase):
    """Google Gemini API provider."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = None

    def _initialize_client(self) -> None:
        """Initialize the Gemini client."""
        try:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
            logger.info(f"Gemini client initialized with model: {self.model}")
        except ImportError:
            raise ImportError("google-genai package not installed. Install with: pip install google-genai")

    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """Send a message to Gemini and get a response."""
        if self._client is None:
            self._initialize_client()

        try:
            # Combine system prompt with user message if provided
            full_prompt = message
            if system_prompt:
                full_prompt = f"{system_prompt}\n\nUser: {message}"

            response = self._client.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                },
            )
            return response.text

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    def chat_stream(self, message: str, system_prompt: Optional[str] = None) -> Iterator[str]:
        """Stream response chunks from Gemini."""
        if self._client is None:
            self._initialize_client()

        try:
            full_prompt = message
            if system_prompt:
                full_prompt = f"{system_prompt}\n\nUser: {message}"

            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=full_prompt,
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                },
            )

            for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            logger.error(f"Gemini streaming error: {e}")
            raise

    def validate_connection(self) -> bool:
        """Test connection to Gemini API."""
        try:
            if self._client is None:
                self._initialize_client()
            # Simple test request
            response = self._client.models.generate_content(
                model=self.model,
                contents="Say 'ok'",
            )
            return bool(response.text)
        except Exception as e:
            logger.error(f"Gemini connection validation failed: {e}")
            return False
