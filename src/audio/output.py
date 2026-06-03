"""Audio output with interruption support."""

import logging
import threading
import time
from typing import Optional

import sounddevice as sd
import numpy as np

from .volume import scale_volume, apply_volume_ramp
from .speaker_mute import SpeakerMute

logger = logging.getLogger(__name__)

# Hardware-mute bracketing around the codec's analog open/close pop. The pop is
# suppressed by muting the speaker amp while the PCM is opened/closed; we unmute
# only after a short preroll of silence has settled, and re-mute on the faded
# tail, so every mute transition happens on a silent signal (pop-free). See
# src/audio/speaker_mute.py and scripts/audio_mute_idle_test.py.
_PREROLL_S = 0.4   # silence played into the muted amp before unmuting
_UNMUTE_AFTER_S = 0.25  # unmute this far into the preroll (still silence)
_TAIL_S = 0.2  # trailing silence so the close happens on a silent signal


def _silence(n: int, like: np.ndarray) -> np.ndarray:
    """n samples of zeros matching like's dtype and channel layout."""
    shape = (n,) if like.ndim == 1 else (n, like.shape[1])
    return np.zeros(shape, dtype=like.dtype)


def _apply_fades(audio: np.ndarray, rate: int, fade_ms: float = 15.0) -> np.ndarray:
    """Ramp the first/last few ms of the buffer to remove the start/end click.

    On this Pi's WM8960 the speaker pops when the first sample steps from zero
    to a non-zero value (a DC discontinuity). A short fade-in eliminates that
    pop; the matching fade-out keeps loop seams (loop=True) and the buffer end
    click-free too. Verified empirically: a 15ms cosine-onset test played
    click-free while a hard 0->peak onset clicked.
    """
    n = audio.shape[0]
    f = min(int(rate * fade_ms / 1000.0), n // 2)
    if f <= 0:
        return audio
    audio = audio.copy()
    ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
    if audio.ndim > 1:  # stereo/multichannel: broadcast ramp across channels
        ramp = ramp[:, np.newaxis]
    audio[:f] = (audio[:f].astype(np.float32) * ramp).astype(audio.dtype)
    audio[-f:] = (audio[-f:].astype(np.float32) * ramp[::-1]).astype(audio.dtype)
    return audio


class AudioOutput:
    """Handles audio playback with interruption support."""

    def __init__(
        self,
        sample_rate: int = 22050,
        volume: int = 100,
        hw_mute: bool = True,
        mute_control: str = "Speaker",
    ):
        self.sample_rate = sample_rate
        self.volume = volume
        self._playing = False
        self._interrupt_flag = threading.Event()
        self._play_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Serializes calls into PortAudio's (non-thread-safe) global play/stop
        # stream so the play thread and interrupt() can't deadlock each other.
        self._pa_lock = threading.Lock()

        # Hardware speaker mute: keep the amp muted while idle (silent next to a
        # bed) and bracket each playback's open/close with it (pop-free). No-op
        # on hardware without the mixer control.
        self._mute = SpeakerMute(control=mute_control, enabled=hw_mute)
        self._stream_unmuted = False
        self._mute.idle()  # start muted: nothing is playing yet

        # Streaming playback (live API): a persistent callback-driven output
        # stream. PortAudio's callback thread pulls from a byte buffer that
        # feed_stream() appends to. Only the main thread ever touches the stream
        # object (start/abort/close) — there is no separate writer thread — so
        # stopping mid-playback can't race PortAudio and segfault.
        self._stream: Optional[sd.RawOutputStream] = None
        self._stream_buf = bytearray()
        self._stream_lock = threading.Lock()
        self._stream_channels = 1

    def play(
        self,
        audio_data: np.ndarray,
        sample_rate: Optional[int] = None,
        loop: bool = False,
        ramp: Optional[tuple] = None,
    ) -> bool:
        """
        Play audio data. Can be interrupted by calling interrupt().

        Args:
            audio_data: Audio samples as numpy array
            sample_rate: Sample rate (uses default if not specified)
            loop: If True, replay the buffer continuously until interrupted.
            ramp: Optional (start_pct, duration_s) ascending-volume ramp. The
                gain rises from start_pct to self.volume over duration_s seconds
                (perceptual/log curve), then holds. None => constant self.volume
                (identical to the no-ramp behavior).

        Returns:
            True if played completely, False if interrupted
        """
        rate = sample_rate or self.sample_rate

        with self._lock:
            self._playing = True
            self._interrupt_flag.clear()

        try:
            # Steady-state buffer (post-ramp / no-ramp): today's exact behavior.
            audio_max = _apply_fades(scale_volume(audio_data, self.volume), rate)
            n_audio = audio_data.shape[0]

            # Ascending-volume setup. We scale a faded, full-amplitude copy by the
            # absolute ramp percent each iteration, so the envelope rides on the
            # same fade shape as audio_max and lands exactly on it when the ramp
            # finishes (amplitude-continuous, no seam pop). `offset` counts only
            # REAL audio samples — the silent preroll must not advance the ramp.
            ramp_active = ramp is not None
            if ramp_active:
                start_pct, duration_s = ramp
                ramp_active = duration_s > 0 and start_pct < self.volume
            if ramp_active:
                faded_full = _apply_fades(audio_data.astype(np.int16, copy=True), rate)
                duration_samples = int(duration_s * rate)
                offset = 0

            def _ramped(off: int) -> np.ndarray:
                return apply_volume_ramp(
                    faded_full, rate, start_pct, self.volume, duration_s,
                    offset_samples=off,
                )

            # Mute the speaker amp before opening the PCM so the codec's analog
            # open-pop is inaudible; prepend silence and unmute partway through
            # it (still silence) so the unmute itself is pop-free. A trailing
            # silence lets the close happen on a silent signal too (non-loop).
            self._mute.mute()
            preroll = _silence(int(rate * _PREROLL_S), audio_max)
            tail = _silence(int(rate * _TAIL_S), audio_max)
            body = _ramped(0) if ramp_active else audio_max
            first = (
                np.concatenate([preroll, body])
                if loop
                else np.concatenate([preroll, body, tail])
            )
            buf = first
            unmuted = False

            # One sd.play per loop iteration. Chunking the buffer and calling
            # sd.play+sd.wait per chunk tears down/reopens the ALSA stream
            # every chunk, which on this Pi multiplies wall-clock by ~5x and
            # produces audible clicks. We poll stream.active instead of
            # sd.wait() — sd.wait() doesn't reliably wake when sd.stop() is
            # called from another thread, leaving is_playing() stuck True.
            #
            # PortAudio's global play/stop stream is NOT thread-safe: if this
            # thread and interrupt() both call into it at once, the call can
            # deadlock. So all sd.play/sd.stop go through self._pa_lock, and on
            # interrupt this thread only sets the flag and exits — interrupt()
            # owns the single sd.stop() that tears the stream down.
            def _interrupted() -> bool:
                if self._interrupt_flag.is_set():
                    self._mute.mute()
                    logger.info("Audio playback interrupted")
                    return True
                return False

            while True:
                if _interrupted():
                    return False
                with self._pa_lock:
                    sd.play(buf, rate)
                    stream = sd.get_stream()
                t0 = time.monotonic()
                while stream.active:
                    if not unmuted and time.monotonic() - t0 >= _UNMUTE_AFTER_S:
                        self._mute.unmute()  # preroll is still playing -> silent
                        unmuted = True
                    if _interrupted():
                        return False
                    time.sleep(0.05)
                # The inner loop also ends when interrupt() stops the stream from
                # the other thread; re-check so a looping alarm doesn't replay.
                if _interrupted():
                    return False
                if not loop:
                    self._mute.mute()  # ended on the faded tail -> mute on silence
                    with self._pa_lock:
                        sd.stop()  # close the PCM while muted (no close-pop)
                    return True
                # Next iteration's buffer (no preroll). Keep ramping until the
                # ramp completes, then hold at the steady-state buffer forever —
                # an unattended looping alarm must NOT drop back to quiet and
                # re-ascend on each loop.
                if ramp_active:
                    offset += n_audio
                    if offset >= duration_samples:
                        ramp_active = False
                        buf = audio_max
                    else:
                        buf = _ramped(offset)
                else:
                    buf = audio_max
                if not unmuted:  # buffer was shorter than the unmute delay
                    self._mute.unmute()
                    unmuted = True

        except Exception as e:
            logger.error(f"Audio playback error: {e}")
            self._mute.mute()
            return False

        finally:
            with self._lock:
                self._playing = False

    def play_async(
        self,
        audio_data: np.ndarray,
        sample_rate: Optional[int] = None,
        loop: bool = False,
        ramp: Optional[tuple] = None,
    ) -> None:
        """Play audio in background thread. See play() for `ramp`."""
        self._play_thread = threading.Thread(
            target=self.play,
            args=(audio_data, sample_rate, loop, ramp),
            daemon=True
        )
        self._play_thread.start()

    # ------------------------------------------------------ streaming playback

    def start_stream(self, sample_rate: Optional[int] = None, channels: int = 1) -> None:
        """Open a persistent callback-driven output stream for streaming PCM.

        Unlike play()/sd.play (which open and close the ALSA device per buffer),
        this keeps one stream open. PortAudio's callback pulls int16 bytes from
        an internal buffer that feed_stream() appends to, so callers (e.g. the
        live API receive loop) never block on ALSA, and stopping mid-playback
        is safe (no writer thread to race the stream teardown). Uses
        sounddevice's default device — the same path the alarm uses.
        """
        rate = sample_rate or self.sample_rate
        self.stop_stream()  # ensure a clean state
        self._stream_channels = channels
        with self._stream_lock:
            self._stream_buf = bytearray()
        # Open muted: the callback feeds silence until real PCM arrives, so the
        # open-pop lands in a muted amp. feed_stream() unmutes on the first audio
        # (which, after network/model latency, is well clear of the open click).
        self._mute.mute()
        self._stream_unmuted = False
        try:
            self._stream = sd.RawOutputStream(
                samplerate=rate,
                channels=channels,
                dtype="int16",
                callback=self._stream_callback,
            )
            self._stream.start()
        except Exception as e:
            logger.error(f"Could not open output stream: {e}")
            self._stream = None
            return
        with self._lock:
            self._playing = True

    def _stream_callback(self, outdata, frames, time_info, status) -> None:
        """PortAudio callback: fill the output buffer from queued PCM bytes.

        Runs on PortAudio's audio thread. Must be fast and must never raise.
        Underruns (not enough buffered audio yet) are filled with silence.
        """
        needed = len(outdata)
        with self._stream_lock:
            n = min(needed, len(self._stream_buf))
            if n:
                outdata[:n] = bytes(self._stream_buf[:n])
                del self._stream_buf[:n]
        if n < needed:
            outdata[n:] = b"\x00" * (needed - n)

    def feed_stream(self, pcm_bytes: bytes) -> None:
        """Append a chunk of raw int16 PCM for playback (non-blocking)."""
        if self._stream is None:
            return
        if self.volume != 100:
            pcm_bytes = scale_volume(pcm_bytes, self.volume)
        with self._stream_lock:
            self._stream_buf.extend(pcm_bytes)
        if not self._stream_unmuted and pcm_bytes:
            # First real audio of this stream: the open-pop has long settled into
            # the silence the callback was feeding, so revealing output is safe.
            self._mute.unmute()
            self._stream_unmuted = True

    def stop_stream(self) -> None:
        """Stop streaming playback, dropping any buffered audio, and close it.

        Only the main thread calls stream methods; PortAudio guarantees the
        callback has finished before abort()/close() return, so this is safe to
        call mid-playback (e.g. a double-tap while the AI is still speaking).
        """
        stream = self._stream
        self._stream = None
        self._stream_unmuted = False
        if stream is not None:
            self._mute.mute()  # mute before teardown so the close-pop is silent
            try:
                stream.abort()  # stop immediately, drop the device buffer
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        with self._stream_lock:
            self._stream_buf = bytearray()
        with self._lock:
            self._playing = False

    # --------------------------------------------------------------- controls

    def interrupt(self) -> None:
        """Interrupt current playback.

        We own the single sd.stop() that tears the stream down; the play thread
        only watches the flag and exits, so the two never call PortAudio's
        global stream concurrently.
        """
        self._interrupt_flag.set()
        self._mute.mute()  # mute first so the teardown is into a silent amp
        with self._pa_lock:
            sd.stop()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=1.0)

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
