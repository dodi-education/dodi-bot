"""Text-to-speech using Piper standalone binary."""

import io
import json
import logging
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np

from audio.volume import scale_volume

logger = logging.getLogger(__name__)

# Default location for piper binary (installed by install.sh)
DEFAULT_PIPER_BINARY = "/usr/local/bin/piper"


class PiperTTS:
    """Text-to-speech using Piper standalone binary.

    Uses the pre-built Piper ARM64 binary which includes all dependencies,
    avoiding the piper-phonemize compilation issues on Raspberry Pi.
    """

    def __init__(self, model_path: str, piper_binary: str = None, volume: int = 100):
        """
        Initialize Piper TTS.

        Args:
            model_path: Path to Piper ONNX model file
            piper_binary: Path to piper binary (auto-detected if not specified)
            volume: Output volume percentage (0-100)
        """
        self.model_path = Path(model_path)
        self.piper_binary = piper_binary
        self.volume = volume
        self._initialized = False
        self.sample_rate = 22050  # Default, will be read from model config

    def initialize(self) -> None:
        """Verify piper binary and model are available."""
        if self._initialized:
            return

        # Find piper binary
        if self.piper_binary is None:
            # Check common locations
            candidates = [
                DEFAULT_PIPER_BINARY,
                str(self.model_path.parent.parent / "piper" / "piper"),
                "/opt/piper/piper",
            ]
            for candidate in candidates:
                if Path(candidate).exists():
                    self.piper_binary = candidate
                    break

        if self.piper_binary is None or not Path(self.piper_binary).exists():
            raise FileNotFoundError(
                f"Piper binary not found. Install it with: sudo ./scripts/install.sh"
            )

        # Verify model exists
        if not self.model_path.exists():
            raise FileNotFoundError(f"Piper model not found: {self.model_path}")

        # Read sample rate from model config
        config_path = Path(str(self.model_path) + ".json")
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                self.sample_rate = config.get("audio", {}).get("sample_rate", 22050)

        # Verify piper works
        result = subprocess.run(
            [self.piper_binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            raise RuntimeError(f"Piper binary not working: {result.stderr}")

        logger.info(f"Piper TTS initialized: {self.piper_binary}")
        logger.info(f"Using model: {self.model_path}")
        logger.info(f"Sample rate: {self.sample_rate}")
        self._initialized = True

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """
        Convert text to speech using Piper.

        Args:
            text: Text to synthesize

        Returns:
            Tuple of (audio_data as numpy array, sample_rate)
        """
        if not self._initialized:
            self.initialize()

        # Run piper to generate raw audio
        # --output-raw outputs 16-bit mono PCM at the model's sample rate
        result = subprocess.run(
            [
                self.piper_binary,
                "--model", str(self.model_path),
                "--output-raw"
            ],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60
        )

        if result.returncode != 0:
            raise RuntimeError(f"Piper synthesis failed: {result.stderr.decode()}")

        # Convert raw PCM bytes to numpy array
        audio_data = np.frombuffer(result.stdout, dtype=np.int16)

        # Apply volume scaling
        audio_data = scale_volume(audio_data, self.volume)

        return audio_data, self.sample_rate

    def speak_async(self, text: str) -> tuple[subprocess.Popen, subprocess.Popen]:
        """
        Start speaking and return process handles for interruption.

        This allows the caller to monitor playback and interrupt if needed.

        Args:
            text: Text to speak

        Returns:
            Tuple of (piper_proc, aplay_proc) for monitoring/termination
        """
        if not self._initialized:
            self.initialize()

        logger.info(f"Speaking: {text[:50]}..." if len(text) > 50 else f"Speaking: {text}")

        # Start piper process
        piper_proc = subprocess.Popen(
            [self.piper_binary, "--model", str(self.model_path), "--output-raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Start aplay process
        aplay_proc = subprocess.Popen(
            ["aplay", "-D", "plughw:1,0", "-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1"],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Send text to piper and close stdin to signal EOF
        piper_proc.stdin.write(text.encode("utf-8"))
        piper_proc.stdin.close()

        # Start background thread to read from piper, apply volume, and write to aplay
        def volume_pipe():
            try:
                while True:
                    chunk = piper_proc.stdout.read(4096)
                    if not chunk:
                        break
                    # Apply volume scaling
                    scaled_chunk = scale_volume(chunk, self.volume)
                    try:
                        aplay_proc.stdin.write(scaled_chunk)
                        aplay_proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        break
            finally:
                try:
                    piper_proc.stdout.close()
                except Exception:
                    pass
                try:
                    aplay_proc.stdin.close()
                except Exception:
                    pass

        pipe_thread = threading.Thread(target=volume_pipe, daemon=True)
        pipe_thread.start()

        return piper_proc, aplay_proc

    def stop_speaking(self, piper_proc: subprocess.Popen, aplay_proc: subprocess.Popen) -> None:
        """
        Terminate playback processes.

        Args:
            piper_proc: The piper process
            aplay_proc: The aplay process
        """
        logger.info("Stopping speech playback")
        try:
            aplay_proc.terminate()
            piper_proc.terminate()
            # Give processes time to terminate gracefully
            aplay_proc.wait(timeout=0.5)
            piper_proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            # Force kill if they don't terminate
            aplay_proc.kill()
            piper_proc.kill()
        except Exception as e:
            logger.warning(f"Error stopping playback: {e}")

    def is_speaking(self, aplay_proc: subprocess.Popen) -> bool:
        """
        Check if playback is still running.

        Args:
            aplay_proc: The aplay process

        Returns:
            True if still playing
        """
        return aplay_proc.poll() is None

    def speak(self, text: str) -> bool:
        """
        Speak text directly (blocking).

        For interruptible speech, use speak_async() instead.

        Args:
            text: Text to speak

        Returns:
            True if playback completed successfully
        """
        piper_proc, aplay_proc = self.speak_async(text)

        # Wait for both processes to complete
        aplay_proc.wait()
        piper_proc.wait()

        if aplay_proc.returncode != 0:
            stderr = aplay_proc.stderr.read().decode() if aplay_proc.stderr else ""
            if "Terminated" not in stderr:  # Don't log error if intentionally stopped
                logger.error(f"aplay failed: {stderr}")
            return False

        if piper_proc.returncode != 0:
            stderr = piper_proc.stderr.read().decode() if piper_proc.stderr else ""
            if "Terminated" not in stderr:
                logger.error(f"piper failed: {stderr}")
            return False

        return True

    def get_sample_rate(self) -> int:
        """Get the output sample rate."""
        return self.sample_rate
