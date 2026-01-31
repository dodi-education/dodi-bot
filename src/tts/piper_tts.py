"""Text-to-speech using Piper."""

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class PiperTTS:
    """Text-to-speech using Piper offline model."""

    def __init__(self, model_path: str):
        """
        Initialize Piper TTS.

        Args:
            model_path: Path to Piper ONNX model file
        """
        self.model_path = Path(model_path)
        self._voice = None
        self.sample_rate = 22050  # Piper default

    def initialize(self) -> None:
        """Load the Piper voice model."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Piper model not found: {self.model_path}")

        try:
            from piper import PiperVoice

            logger.info(f"Loading Piper voice from {self.model_path}...")
            self._voice = PiperVoice.load(str(self.model_path))
            logger.info("Piper voice loaded")

        except ImportError:
            raise ImportError("piper-tts package not installed")

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """
        Convert text to speech.

        Args:
            text: Text to synthesize

        Returns:
            Tuple of (audio_data as numpy array, sample_rate)
        """
        if self._voice is None:
            self.initialize()

        # Get sample rate from voice config
        self.sample_rate = self._voice.config.sample_rate

        # Synthesize to raw audio bytes
        audio_chunks = []
        for audio_bytes in self._voice.synthesize_stream_raw(text):
            audio_chunks.append(audio_bytes)

        # Combine all chunks and convert to numpy array
        raw_audio = b''.join(audio_chunks)
        audio_data = np.frombuffer(raw_audio, dtype=np.int16)

        return audio_data, self.sample_rate

    def get_sample_rate(self) -> int:
        """Get the output sample rate."""
        return self.sample_rate
