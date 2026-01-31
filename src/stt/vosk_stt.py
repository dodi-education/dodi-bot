"""Speech-to-text using Vosk."""

import json
import logging
from pathlib import Path
from typing import Optional

from vosk import Model, KaldiRecognizer

logger = logging.getLogger(__name__)


class VoskSTT:
    """Speech-to-text using Vosk offline model."""

    def __init__(self, model_path: str, sample_rate: int = 16000):
        """
        Initialize Vosk STT.

        Args:
            model_path: Path to Vosk model directory
            sample_rate: Audio sample rate (must match audio input)
        """
        self.model_path = Path(model_path)
        self.sample_rate = sample_rate
        self._model: Optional[Model] = None
        self._recognizer: Optional[KaldiRecognizer] = None

    def initialize(self) -> None:
        """Load the Vosk model."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Vosk model not found: {self.model_path}")

        logger.info(f"Loading Vosk model from {self.model_path}...")
        self._model = Model(str(self.model_path))
        self._recognizer = KaldiRecognizer(self._model, self.sample_rate)
        self._recognizer.SetWords(True)
        logger.info("Vosk model loaded")

    def process_audio(self, audio_data: bytes) -> Optional[str]:
        """
        Process audio chunk and return partial/final transcription.

        Args:
            audio_data: Raw audio bytes (16-bit PCM)

        Returns:
            Transcribed text if speech detected, None otherwise
        """
        if self._recognizer is None:
            self.initialize()

        if self._recognizer.AcceptWaveform(audio_data):
            result = json.loads(self._recognizer.Result())
            text = result.get("text", "").strip()
            if text:
                return text
        return None

    def get_partial(self) -> str:
        """Get partial transcription result."""
        if self._recognizer is None:
            return ""
        result = json.loads(self._recognizer.PartialResult())
        return result.get("partial", "").strip()

    def reset(self) -> None:
        """Reset the recognizer for a new utterance."""
        if self._model:
            self._recognizer = KaldiRecognizer(self._model, self.sample_rate)
            self._recognizer.SetWords(True)

    def finalize(self) -> str:
        """Get final result and reset."""
        if self._recognizer is None:
            return ""
        result = json.loads(self._recognizer.FinalResult())
        text = result.get("text", "").strip()
        self.reset()
        return text
