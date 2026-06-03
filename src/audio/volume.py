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


def apply_volume_ramp(
    audio: np.ndarray,
    rate: int,
    start_pct: float,
    end_pct: float,
    duration_s: float,
    offset_samples: int = 0,
) -> np.ndarray:
    """Scale `audio` by a smooth, perceptual (logarithmic) volume ramp.

    The gain rises geometrically from `start_pct` to `end_pct` over absolute
    playback time `[0, duration_s]`, then holds at `end_pct`. "Geometric" means
    the curve is exponential in amplitude / linear in dB, so the loudness rises
    evenly rather than jumping up early (the chosen feel for an ascending alarm).

    The ramp is keyed to ABSOLUTE time: sample k of this buffer sits at time
    `(offset_samples + k) / rate`. Pass `offset_samples` so a ramp can continue
    seamlessly across a buffer that is replayed (looped) — the gain depends on
    how much real audio has played, not on this buffer's position.

    Args:
        audio: int16 samples, mono (1D) or multichannel (2D, frames x channels).
        rate: sample rate in Hz.
        start_pct, end_pct: volume percentages 0-100 (as in scale_volume).
        duration_s: seconds over which the ramp rises.
        offset_samples: absolute sample index of this buffer's first sample.

    Returns:
        A new int16 array of the same shape.
    """
    n = audio.shape[0]
    # Degenerate ramp -> constant end volume (same as a plain scale).
    if duration_s <= 0 or start_pct == end_pct:
        return scale_volume(audio, int(round(end_pct)))

    # A geometric ramp is undefined at 0, so floor the endpoints to ~1% (-40 dB,
    # effectively silent) to keep the curve well-defined when start_volume is 0.
    start = max(1.0, float(start_pct))
    end = max(1.0, float(end_pct))
    dur_samples = max(1.0, duration_s * rate)

    idx = np.arange(offset_samples, offset_samples + n, dtype=np.float64)
    frac = np.clip(idx / dur_samples, 0.0, 1.0)          # 0 at t=0, 1 at t>=dur
    pct = start * (end / start) ** frac                  # start@frac0 -> end@frac1
    gain = np.clip(pct / 100.0, 0.0, 1.0).astype(np.float32)

    if audio.ndim > 1:  # broadcast the per-frame gain across channels
        gain = gain[:, np.newaxis]

    scaled = audio.astype(np.float32) * gain
    np.clip(scaled, -32768.0, 32767.0, out=scaled)
    return scaled.astype(np.int16)
