"""OpenAI ChatGPT provider implementation."""

import logging
from typing import Iterator, Optional

from .provider_base import AIProviderBase

logger = logging.getLogger(__name__)


class OpenAIProvider(AIProviderBase):
    """OpenAI ChatGPT API provider."""

    def _initialize_client(self) -> None:
        """Initialize the OpenAI client."""
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
            logger.info(f"OpenAI client initialized with model: {self.model}")
        except ImportError:
            raise ImportError("openai package not installed")

    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """Send a message to OpenAI and get a response."""
        if self._client is None:
            self._initialize_client()

        try:
            messages = []

            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            messages.append({"role": "user", "content": message})

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    def chat_stream(self, message: str, system_prompt: Optional[str] = None) -> Iterator[str]:
        """Stream response chunks from OpenAI."""
        if self._client is None:
            self._initialize_client()

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": message})

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
            )

            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise

    def validate_connection(self) -> bool:
        """Test connection to OpenAI API."""
        try:
            if self._client is None:
                self._initialize_client()
            # Simple test request
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Say 'ok'"}],
                max_tokens=10,
            )
            return bool(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"OpenAI connection validation failed: {e}")
            return False
