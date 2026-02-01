"""xAI Grok provider implementation."""

import logging
from typing import Iterator, List, Optional

from .provider_base import AIProviderBase

logger = logging.getLogger(__name__)


class GrokProvider(AIProviderBase):
    """xAI Grok API provider (uses OpenAI-compatible API)."""

    GROK_BASE_URL = "https://api.x.ai/v1"

    def _initialize_client(self) -> None:
        """Initialize the Grok client using OpenAI SDK."""
        try:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.GROK_BASE_URL,
            )
            logger.info(f"Grok client initialized with model: {self.model}")
        except ImportError:
            raise ImportError("openai package not installed (required for Grok)")

    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """Send a message to Grok and get a response."""
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
            logger.error(f"Grok API error: {e}")
            raise

    def chat_stream(self, message: str, system_prompt: Optional[str] = None) -> Iterator[str]:
        """Stream response chunks from Grok."""
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
            logger.error(f"Grok streaming error: {e}")
            raise

    def chat_with_history(
        self,
        messages: List[dict],
        system_prompt: Optional[str] = None
    ) -> str:
        """Send conversation with history to Grok."""
        if self._client is None:
            self._initialize_client()

        try:
            api_messages = []
            if system_prompt:
                api_messages.append({"role": "system", "content": system_prompt})
            api_messages.extend(messages)

            response = self._client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Grok API error: {e}")
            raise

    def chat_stream_with_history(
        self,
        messages: List[dict],
        system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        """Stream response from Grok with history."""
        if self._client is None:
            self._initialize_client()

        try:
            api_messages = []
            if system_prompt:
                api_messages.append({"role": "system", "content": system_prompt})
            api_messages.extend(messages)

            response = self._client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
            )

            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"Grok streaming error: {e}")
            raise

    def validate_connection(self) -> bool:
        """Test connection to Grok API."""
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
            logger.error(f"Grok connection validation failed: {e}")
            return False
