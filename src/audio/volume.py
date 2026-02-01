"""Audio volume utilities."""

import struct
from typing import Union

import numpy as np


def scale_volume(audio_data: Union[bytes, np.ndarray], volume: int) -> Union[bytes, np.ndarray]:
    """
    Scale PCM16 audio data by a volume percentage.

    Args:
        audio_data: Raw PCM16 audio as bytes or numpy int16 array
        volume: Volume percentage from 0 (muted) to 100 (full volume)

    Returns:
        Scaled audio data in the same format as input
    """
    # Convert volume 0-100 to multiplier 0.0-1.0
    multiplier = max(0.0, min(1.0, volume / 100.0))

    if multiplier == 1.0:
        return audio_data

    if isinstance(audio_data, bytes):
        # Handle raw bytes (PCM16 little-endian)
        num_samples = len(audio_data) // 2
        if num_samples == 0:
            return audio_data

        samples = struct.unpack(f'<{num_samples}h', audio_data)
        scaled = [int(max(-32768, min(32767, s * multiplier))) for s in samples]
        return struct.pack(f'<{len(scaled)}h', *scaled)

    elif isinstance(audio_data, np.ndarray):
        # Handle numpy array
        scaled = (audio_data.astype(np.float32) * multiplier).astype(np.int16)
        return scaled

    else:
        raise TypeError(f"Unsupported audio data type: {type(audio_data)}")
