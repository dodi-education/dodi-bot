"""Streaming speech manager for low-latency voice output."""

import logging
import threading
import queue
import time
from typing import Iterator, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SpeechState(Enum):
    """Current state of the streaming speech manager."""
    IDLE = "idle"
    STREAMING = "streaming"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


@dataclass
class SpeechSegment:
    """A segment of text to be spoken."""
    text: str
    index: int
    is_final: bool = False


class StreamingSpeechManager:
    """
    Manages streaming text-to-speech with interruption support.

    Coordinates:
    - Receiving text chunks from LLM
    - Buffering and extracting sentences
    - Queuing sentences for TTS
    - Playing audio while monitoring for interruption
    """

    def __init__(
        self,
        tts,
        stt,
        audio_input,
        stop_words: list,
        debug: bool = False
    ):
        """
        Initialize the streaming speech manager.

        Args:
            tts: PiperTTS instance
            stt: VoskSTT instance for stop word detection
            audio_input: AudioInput instance
            stop_words: List of words that trigger interruption
            debug: Enable debug logging
        """
        self.tts = tts
        self.stt = stt
        self.audio_input = audio_input
        self.stop_words = [w.lower() for w in stop_words]
        self.debug = debug

        self._state = SpeechState.IDLE
        self._sentence_queue: queue.Queue = queue.Queue()
        self._interrupt_flag = threading.Event()
        self._current_processes: Optional[tuple] = None
        self._lock = threading.Lock()

        # Statistics
        self._stats = {
            "sentences_received": 0,
            "sentences_spoken": 0,
            "time_to_first_speech": 0,
        }

    def _contains_stop_word(self, text: str) -> bool:
        """Check if text contains a stop word."""
        text_lower = text.lower()
        return any(stop_word in text_lower for stop_word in self.stop_words)

    def stream_and_speak(self, text_stream: Iterator[str]) -> bool:
        """
        Stream text chunks, buffer into sentences, and speak them.

        Args:
            text_stream: Iterator yielding text chunks from LLM

        Returns:
            True if completed normally, False if interrupted
        """
        from utils.sentence_buffer import SentenceBuffer

        with self._lock:
            self._state = SpeechState.STREAMING
            self._interrupt_flag.clear()
            self._stats["time_to_first_speech"] = 0
            self._stats["sentences_received"] = 0
            self._stats["sentences_spoken"] = 0

        start_time = time.time()
        buffer = SentenceBuffer()
        segment_index = 0
        first_speech_logged = False

        try:
            # Start the speaker thread
            speaker_thread = threading.Thread(
                target=self._speaker_loop,
                daemon=True
            )
            speaker_thread.start()

            # Process incoming text stream
            for chunk in text_stream:
                if self._interrupt_flag.is_set():
                    logger.info("Stream interrupted by stop word")
                    break

                if self.debug:
                    print(f"[DEBUG] LLM chunk: {chunk}", end="", flush=True)

                # Extract complete sentences
                sentences = buffer.add(chunk)

                for sentence in sentences:
                    if self._interrupt_flag.is_set():
                        break

                    segment = SpeechSegment(
                        text=sentence,
                        index=segment_index,
                        is_final=False
                    )
                    self._sentence_queue.put(segment)
                    segment_index += 1
                    self._stats["sentences_received"] += 1

                    if not first_speech_logged:
                        self._stats["time_to_first_speech"] = time.time() - start_time
                        logger.info(f"Time to first sentence: {self._stats['time_to_first_speech']:.2f}s")
                        first_speech_logged = True

            # Get any remaining text
            if not self._interrupt_flag.is_set():
                final_text = buffer.flush()
                if final_text:
                    segment = SpeechSegment(
                        text=final_text,
                        index=segment_index,
                        is_final=True
                    )
                    self._sentence_queue.put(segment)
                    self._stats["sentences_received"] += 1

            # Signal end of stream
            self._sentence_queue.put(None)

            # Wait for speaker to finish
            speaker_thread.join(timeout=60)

            with self._lock:
                if self._interrupt_flag.is_set():
                    self._state = SpeechState.INTERRUPTED
                    return False
                else:
                    self._state = SpeechState.COMPLETED
                    return True

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            self._sentence_queue.put(None)
            with self._lock:
                self._state = SpeechState.INTERRUPTED
            return False

    def _speaker_loop(self):
        """Background thread that speaks queued sentences."""
        self.stt.reset()

        while True:
            try:
                segment = self._sentence_queue.get(timeout=0.1)
            except queue.Empty:
                # Check for interruption while waiting
                if self._interrupt_flag.is_set():
                    break
                self._check_for_interrupt()
                continue

            if segment is None:
                break

            if self._interrupt_flag.is_set():
                break

            # Speak this segment
            logger.info(f"Speaking segment {segment.index}: {segment.text[:50]}...")
            success = self._speak_segment(segment.text)

            if not success:
                logger.info("Speech interrupted")
                break

            self._stats["sentences_spoken"] += 1

    def _speak_segment(self, text: str) -> bool:
        """
        Speak a single text segment while monitoring for interruption.

        Returns:
            True if completed, False if interrupted
        """
        piper_proc, aplay_proc = self.tts.speak_async(text)

        with self._lock:
            self._current_processes = (piper_proc, aplay_proc)
            self._state = SpeechState.SPEAKING

        try:
            while self.tts.is_speaking(aplay_proc):
                if self._interrupt_flag.is_set():
                    self._stop_current_speech()
                    return False

                self._check_for_interrupt()

            piper_proc.wait()
            return aplay_proc.returncode == 0

        finally:
            with self._lock:
                self._current_processes = None

    def _check_for_interrupt(self):
        """Check for stop words in audio input."""
        try:
            audio_data = self.audio_input.read(timeout=0.05)
            if audio_data:
                result = self.stt.process_audio(audio_data)
                if result and self._contains_stop_word(result):
                    logger.info(f"Stop word detected: {result}")
                    self._interrupt_flag.set()
                    self._stop_current_speech()
        except Exception:
            pass

    def _stop_current_speech(self):
        """Stop current TTS playback."""
        with self._lock:
            if self._current_processes:
                piper_proc, aplay_proc = self._current_processes
                self.tts.stop_speaking(piper_proc, aplay_proc)

    def interrupt(self):
        """Externally trigger an interruption."""
        self._interrupt_flag.set()
        self._stop_current_speech()

        while True:
            try:
                self._sentence_queue.get_nowait()
            except queue.Empty:
                break

    def get_state(self) -> SpeechState:
        """Get current state."""
        with self._lock:
            return self._state

    def get_stats(self) -> dict:
        """Get statistics for debugging."""
        return self._stats.copy()
