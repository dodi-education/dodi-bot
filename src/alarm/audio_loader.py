"""Load alarm audio files into numpy arrays.

WAV is handled with stdlib `wave` (no deps). Anything else (mp3, ogg, flac,
m4a, ...) goes through pydub, which shells out to ffmpeg.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("DODI.alarm")


def load_audio(path: Path) -> Optional[tuple[np.ndarray, int]]:
    """Load an audio file and return (samples_int16, sample_rate).

    Returns None on any error (missing file, unsupported format, decoder
    unavailable, ...). Mono and multichannel inputs are both accepted;
    multichannel is downmixed to mono.
    """
    if not path.exists():
        logger.warning(f"Alarm audio file not found: {path}")
        return None

    suffix = path.suffix.lower()
    if suffix == ".wav":
        return _load_wav(path)
    return _load_via_pydub(path)


def _load_wav(path: Path) -> Optional[tuple[np.ndarray, int]]:
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except Exception as e:
        logger.error(f"Failed to read WAV {path}: {e}")
        return None

    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 1:
        u8 = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        samples = (u8 - 128) * 256
    elif sampwidth == 4:
        i32 = np.frombuffer(raw, dtype=np.int32)
        samples = (i32 >> 16).astype(np.int16)
    else:
        logger.error(f"Unsupported WAV sample width: {sampwidth} bytes")
        return None

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

    return samples, sample_rate


def _load_via_pydub(path: Path) -> Optional[tuple[np.ndarray, int]]:
    try:
        from pydub import AudioSegment
    except ImportError:
        logger.error(
            f"Cannot decode {path.name}: pydub not installed "
            "(pip install pydub; apt install ffmpeg)"
        )
        return None

    try:
        seg = AudioSegment.from_file(str(path))
    except Exception as e:
        logger.error(f"Failed to decode {path}: {e}")
        return None

    # Force 16-bit; pydub raw samples are little-endian PCM in that case.
    if seg.sample_width != 2:
        seg = seg.set_sample_width(2)

    samples = np.frombuffer(seg.raw_data, dtype=np.int16)
    if seg.channels > 1:
        samples = samples.reshape(-1, seg.channels).mean(axis=1).astype(np.int16)
    return samples, seg.frame_rate
