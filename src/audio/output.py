"""Audio output with interruption support."""

import logging
import threading
import queue
import time
from typing import Optional

import sounddevice as sd
import numpy as np

logger = logging.getLogger(__name__)


class AudioOutput:
    """Handles audio playback with interruption support."""

    def __init__(self, sample_rate: int = 22050):
        self.sample_rate = sample_rate
        self._playing = False
        self._interrupt_flag = threading.Event()
        self._play_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def play(self, audio_data: np.ndarray, sample_rate: Optional[int] = None) -> bool:
        """
        Play audio data. Can be interrupted by calling interrupt().

        Args:
            audio_data: Audio samples as numpy array
            sample_rate: Sample rate (uses default if not specified)

        Returns:
            True if played completely, False if interrupted
        """
        rate = sample_rate or self.sample_rate

        with self._lock:
            self._playing = True
            self._interrupt_flag.clear()

        try:
            # Play in chunks to allow interruption
            chunk_size = int(rate * 0.1)  # 100ms chunks
            total_samples = len(audio_data)

            for i in range(0, total_samples, chunk_size):
                if self._interrupt_flag.is_set():
                    logger.info("Audio playback interrupted")
                    return False

                chunk = audio_data[i:i + chunk_size]
                sd.play(chunk, rate)
                sd.wait()

            return True

        except Exception as e:
            logger.error(f"Audio playback error: {e}")
            return False

        finally:
            with self._lock:
                self._playing = False

    def play_async(self, audio_data: np.ndarray, sample_rate: Optional[int] = None) -> None:
        """Play audio in background thread."""
        self._play_thread = threading.Thread(
            target=self.play,
            args=(audio_data, sample_rate),
            daemon=True
        )
        self._play_thread.start()

    def interrupt(self) -> None:
        """Interrupt current playback."""
        self._interrupt_flag.set()
        sd.stop()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=0.5)

    def is_playing(self) -> bool:
        """Check if audio is currently playing."""
        with self._lock:
            return self._playing

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for playback to complete."""
        if self._play_thread:
            self._play_thread.join(timeout=timeout)
            return not self._play_thread.is_alive()
        return True
