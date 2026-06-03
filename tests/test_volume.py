"""Unit tests for the volume helpers, focused on apply_volume_ramp."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audio.volume import apply_volume_ramp, scale_volume  # noqa: E402

RATE = 1000  # 1 sample per ms keeps the math easy to reason about


def _const(n, amp=10000):
    return np.full(n, amp, dtype=np.int16)


def test_endpoints_hit_start_and_end():
    """Gain at t=0 is start_pct; at t>=duration it is end_pct."""
    audio = _const(RATE * 4)  # 4s
    out = apply_volume_ramp(audio, RATE, start_pct=10, end_pct=50, duration_s=2.0)
    # sample 0 -> 10% of 10000
    assert abs(int(out[0]) - 1000) <= 2
    # sample at t=2s (index 2000) and beyond -> 50%
    assert abs(int(out[RATE * 2]) - 5000) <= 2
    assert abs(int(out[-1]) - 5000) <= 2


def test_monotonic_non_decreasing_during_ramp():
    audio = _const(RATE * 3)
    out = apply_volume_ramp(audio, RATE, 10, 80, duration_s=3.0).astype(np.int32)
    assert np.all(np.diff(out) >= 0)


def test_curve_is_geometric():
    """Equal time steps multiply the gain by a constant ratio (linear in dB)."""
    audio = _const(RATE * 4)
    out = apply_volume_ramp(audio, RATE, 10, 160, duration_s=4.0).astype(np.float64)
    # samples at t=0,1,2,3,4s
    pts = out[[0, RATE, RATE * 2, RATE * 3, RATE * 4 - 1]]
    ratios = pts[1:] / pts[:-1]
    # 10 -> 160 over 4s geometric => ratio per second = (160/10)**(1/4) = 2.0
    assert np.allclose(ratios[:-1], 2.0, rtol=0.05)


def test_holds_at_end_after_duration():
    audio = _const(RATE * 5)
    out = apply_volume_ramp(audio, RATE, 10, 50, duration_s=2.0)
    expected = scale_volume(audio, 50)
    # everything from t>=2s equals a plain 50% scale
    assert np.array_equal(out[RATE * 2:], expected[RATE * 2:])


def test_offset_continuity_across_buffers():
    """Ramping a buffer with offset=k equals slicing the full ramp at [k:]."""
    full = _const(RATE * 4)
    whole = apply_volume_ramp(full, RATE, 10, 50, duration_s=4.0)
    k = RATE  # start the second buffer 1s in
    piece = apply_volume_ramp(full[k:], RATE, 10, 50, duration_s=4.0, offset_samples=k)
    assert np.array_equal(piece, whole[k:])


def test_degenerate_duration_zero_is_constant():
    audio = _const(RATE)
    assert np.array_equal(
        apply_volume_ramp(audio, RATE, 10, 50, duration_s=0.0),
        scale_volume(audio, 50),
    )


def test_degenerate_equal_endpoints_is_constant():
    audio = _const(RATE)
    assert np.array_equal(
        apply_volume_ramp(audio, RATE, 40, 40, duration_s=2.0),
        scale_volume(audio, 40),
    )


def test_start_floored_above_zero():
    """start_pct=0 must not blow up (geometric ramp floors to ~1%)."""
    audio = _const(RATE * 3)  # longer than the 2s ramp so the tail is the held value
    out = apply_volume_ramp(audio, RATE, start_pct=0, end_pct=50, duration_s=2.0)
    assert int(out[0]) > 0           # not silent-zero / not NaN
    assert int(out[0]) <= 200        # ~1% of 10000 = 100
    assert abs(int(out[-1]) - 5000) <= 2   # held at end_pct past t=duration


def test_stereo_broadcast_keeps_shape():
    audio = np.full((RATE * 2, 2), 10000, dtype=np.int16)
    out = apply_volume_ramp(audio, RATE, 10, 50, duration_s=2.0)
    assert out.shape == audio.shape
    # both channels get the same per-frame gain
    assert np.array_equal(out[:, 0], out[:, 1])


def test_no_overflow_at_full_scale():
    audio = np.full(RATE, 32767, dtype=np.int16)
    out = apply_volume_ramp(audio, RATE, 10, 100, duration_s=1.0)
    assert out.dtype == np.int16
    assert int(np.abs(out).max()) <= 32767
