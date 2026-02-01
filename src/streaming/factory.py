"""Factory for creating voice streaming providers."""

import os
import logging
from typing import Optional

from .provider_base import VoiceStreamingProvider
from .openai_realtime import OpenAIRealtimeProvider
from .grok_realtime import GrokRealtimeProvider
from .gemini_live import GeminiLiveProvider

logger = logging.getLogger(__name__)

# Map provider names to classes
PROVIDERS = {
    "openai": OpenAIRealtimeProvider,
    "grok": GrokRealtimeProvider,
    "gemini": GeminiLiveProvider,
}

# Map provider names to environment variable names for API keys
API_KEY_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "grok": "GROK_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def create_streaming_provider(
    provider_name: str,
    config: dict,
    api_key: Optional[str] = None,
    input_sample_rate: int = None,
    volume: int = 100
) -> VoiceStreamingProvider:
    """
    Create a voice streaming provider based on configuration.

    Args:
        provider_name: Name of the provider (openai, grok, gemini)
        config: Provider-specific configuration dict
        api_key: Optional API key (if not provided, reads from environment)
        input_sample_rate: Sample rate of input audio from microphone
        volume: Output volume percentage (0-100)

    Returns:
        Configured VoiceStreamingProvider instance

    Raises:
        ValueError: If provider is not supported
        ValueError: If API key is not found
    """
    provider_name = provider_name.lower()

    if provider_name not in PROVIDERS:
        raise ValueError(
            f"Unsupported streaming provider: {provider_name}. "
            f"Supported: {', '.join(PROVIDERS.keys())}"
        )

    # Get API key
    if api_key is None:
        env_var = API_KEY_ENV_VARS.get(provider_name)
        api_key = os.environ.get(env_var)

    if not api_key:
        raise ValueError(
            f"API key not found for {provider_name}. "
            f"Set {API_KEY_ENV_VARS.get(provider_name)} environment variable."
        )

    # Get provider class and create instance
    provider_class = PROVIDERS[provider_name]

    model = config.get("model")
    voice = config.get("voice")

    logger.info(f"Creating {provider_name} streaming provider with model={model}, voice={voice}, volume={volume}")

    # Build kwargs
    kwargs = {
        "api_key": api_key,
        "model": model,
        "voice": voice,
        "volume": volume
    }
    if input_sample_rate is not None and provider_name in ("openai", "grok"):
        kwargs["input_sample_rate"] = input_sample_rate

    return provider_class(**kwargs)


def get_available_providers() -> list:
    """Return list of available streaming provider names."""
    return list(PROVIDERS.keys())


def is_provider_available(provider_name: str) -> bool:
    """Check if a provider is available (has API key set)."""
    provider_name = provider_name.lower()

    if provider_name not in PROVIDERS:
        return False

    env_var = API_KEY_ENV_VARS.get(provider_name)
    return bool(os.environ.get(env_var))
