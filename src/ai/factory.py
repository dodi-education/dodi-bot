"""AI Provider factory for creating provider instances."""

import os
import logging
from typing import Optional

from .provider_base import AIProviderBase
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .grok_provider import GrokProvider

logger = logging.getLogger(__name__)

# Provider registry
PROVIDERS = {
    "gemini": {
        "class": GeminiProvider,
        "env_key": "GEMINI_API_KEY",
    },
    "openai": {
        "class": OpenAIProvider,
        "env_key": "OPENAI_API_KEY",
    },
    "grok": {
        "class": GrokProvider,
        "env_key": "GROK_API_KEY",
    },
}


def create_provider(
    provider_name: str,
    config: dict,
    api_key: Optional[str] = None,
) -> AIProviderBase:
    """
    Create an AI provider instance.

    Args:
        provider_name: Name of the provider ("gemini", "openai", "grok")
        config: Provider-specific configuration from settings.json
        api_key: Optional API key (defaults to environment variable)

    Returns:
        Configured AIProviderBase instance

    Raises:
        ValueError: If provider is not supported
        ValueError: If API key is not found
    """
    provider_name = provider_name.lower()

    if provider_name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            f"Supported: {list(PROVIDERS.keys())}"
        )

    provider_info = PROVIDERS[provider_name]

    # Get API key from argument or environment
    if api_key is None:
        api_key = os.getenv(provider_info["env_key"])

    if not api_key:
        raise ValueError(
            f"API key not found for {provider_name}. "
            f"Set {provider_info['env_key']} environment variable or pass api_key argument."
        )

    # Create provider instance
    provider_class = provider_info["class"]
    provider = provider_class(
        api_key=api_key,
        model=config.get("model"),
        temperature=config.get("temperature", 0.7),
        max_tokens=config.get("max_tokens", 512),
    )

    logger.info(f"Created {provider_name} provider with model: {config.get('model')}")
    return provider


def list_providers() -> list[str]:
    """Return list of supported provider names."""
    return list(PROVIDERS.keys())
