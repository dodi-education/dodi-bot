"""Google Gemini AI provider implementation."""

import logging
from typing import Optional

from .provider_base import AIProviderBase

logger = logging.getLogger(__name__)


class GeminiProvider(AIProviderBase):
    """Google Gemini API provider."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._genai = None
        self._model = None

    def _initialize_client(self) -> None:
        """Initialize the Gemini client."""
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            self._genai = genai
            self._model = genai.GenerativeModel(
                model_name=self.model,
                generation_config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                },
            )
            logger.info(f"Gemini client initialized with model: {self.model}")
        except ImportError:
            raise ImportError("google-generativeai package not installed")

    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """Send a message to Gemini and get a response."""
        if self._model is None:
            self._initialize_client()

        try:
            # Combine system prompt with user message if provided
            full_prompt = message
            if system_prompt:
                full_prompt = f"{system_prompt}\n\nUser: {message}"

            response = self._model.generate_content(full_prompt)
            return response.text

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    def validate_connection(self) -> bool:
        """Test connection to Gemini API."""
        try:
            if self._model is None:
                self._initialize_client()
            # Simple test request
            response = self._model.generate_content("Say 'ok'")
            return bool(response.text)
        except Exception as e:
            logger.error(f"Gemini connection validation failed: {e}")
            return False
