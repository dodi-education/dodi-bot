"""Audio input capture using PyAudio."""

import logging
import threading
import queue
from typing import Callable, Optional

import pyaudio

logger = logging.getLogger(__name__)


class AudioInput:
    """Handles microphone audio capture with callback support."""

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        channels: int = 1,
        device_name: Optional[str] = None,
    ):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.device_name = device_name

        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._running = False
        self._audio_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    def _find_device_index(self) -> Optional[int]:
        """Find input device by name."""
        if not self.device_name:
            return None

        for i in range(self._pyaudio.get_device_count()):
            info = self._pyaudio.get_device_info_by_index(i)
            if self.device_name.lower() in info["name"].lower():
                if info["maxInputChannels"] > 0:
                    logger.info(f"Found input device: {info['name']} (index {i})")
                    return i
        logger.warning(f"Device '{self.device_name}' not found, using default")
        return None

    def start(self) -> None:
        """Start audio capture."""
        if self._running:
            return

        self._pyaudio = pyaudio.PyAudio()
        device_index = self._find_device_index()

        self._stream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=self.chunk_size,
        )

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Audio input started")

    def _capture_loop(self) -> None:
        """Continuously capture audio and put in queue."""
        while self._running:
            try:
                data = self._stream.read(self.chunk_size, exception_on_overflow=False)
                self._audio_queue.put(data)
            except Exception as e:
                if self._running:
                    logger.error(f"Audio capture error: {e}")

    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        """Read audio chunk from queue."""
        try:
            return self._audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear_queue(self) -> None:
        """Clear any buffered audio."""
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        """Stop audio capture."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if self._pyaudio:
            self._pyaudio.terminate()
            self._pyaudio = None

        logger.info("Audio input stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
