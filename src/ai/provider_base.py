"""Abstract base class for AI providers."""

from abc import ABC, abstractmethod
from typing import Iterator, List, Optional


class AIProviderBase(ABC):
    """Unified interface for all AI providers (Gemini, OpenAI, Grok)."""

    def __init__(self, api_key: str, model: str, temperature: float = 0.7, max_tokens: int = 512):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None

    @abstractmethod
    def _initialize_client(self) -> None:
        """Initialize the provider-specific client."""
        pass

    @abstractmethod
    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """
        Send a message and get a response.

        Args:
            message: User message text
            system_prompt: Optional system prompt (persona)

        Returns:
            Response text from the AI
        """
        pass

    @abstractmethod
    def chat_stream(self, message: str, system_prompt: Optional[str] = None) -> Iterator[str]:
        """
        Send a message and stream response chunks as they arrive.

        Args:
            message: User message text
            system_prompt: Optional system prompt (persona)

        Yields:
            Response text chunks as they are generated
        """
        pass

    @abstractmethod
    def chat_with_history(
        self,
        messages: List[dict],
        system_prompt: Optional[str] = None
    ) -> str:
        """
        Send a conversation with history and get a response.

        Args:
            messages: List of {"role": "user"|"assistant", "content": str}
            system_prompt: Optional system prompt (persona)

        Returns:
            Response text from the AI
        """
        pass

    @abstractmethod
    def chat_stream_with_history(
        self,
        messages: List[dict],
        system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        """
        Stream a response given conversation history.

        Args:
            messages: List of {"role": "user"|"assistant", "content": str}
            system_prompt: Optional system prompt (persona)

        Yields:
            Response text chunks as they are generated
        """
        pass

    @abstractmethod
    def validate_connection(self) -> bool:
        """
        Test if the API connection is working.

        Returns:
            True if connection is valid, False otherwise
        """
        pass

    def get_provider_name(self) -> str:
        """Return the provider name for logging."""
        return self.__class__.__name__.replace("Provider", "")
