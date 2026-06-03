"""Two-mode operating state for DODI.

The bot has exactly two modes:

* ``DEAF``   — microphone off (privacy default). The avatar wears headphones.
* ``NORMAL`` — microphone on, streaming a live conversation to the AI provider.

A double-tap on the screen toggles between them; an inactivity timeout drops
NORMAL back to DEAF automatically.
"""

import time
from enum import Enum
from typing import Callable, Optional


class Mode(Enum):
    """Top-level operating mode."""
    DEAF = "deaf"
    NORMAL = "normal"


class InactivityTimer:
    """Counts down from the last activity; drives the auto-return to DEAF.

    Conversation activity (user or AI speech) calls :meth:`mark`. Once more than
    ``timeout_seconds`` elapse without a ``mark``, :meth:`expired` returns True.
    The clock is injectable so the timer can be unit-tested without sleeping.
    """

    def __init__(
        self,
        timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._last_activity: Optional[float] = None

    def mark(self) -> None:
        """Record activity now (restart the countdown)."""
        self._last_activity = self._clock()

    def clear(self) -> None:
        """Stop the countdown until the next :meth:`mark`."""
        self._last_activity = None

    def expired(self) -> bool:
        """True once ``timeout_seconds`` have elapsed since the last activity.

        Returns False while the timer is cleared (never started).
        """
        if self._last_activity is None:
            return False
        return self._clock() - self._last_activity >= self.timeout_seconds

    def remaining(self) -> float:
        """Seconds left before expiry (full timeout while cleared)."""
        if self._last_activity is None:
            return self.timeout_seconds
        return max(0.0, self.timeout_seconds - (self._clock() - self._last_activity))
