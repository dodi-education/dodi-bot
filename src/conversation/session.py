"""Conversation session management for active mode control."""

import time
from enum import Enum
from typing import Optional

from .history import ConversationHistory


class SessionState(Enum):
    """States of the conversation session."""
    PASSIVE = "passive"              # Wake word listening only
    ACTIVE_LISTENING = "listening"   # Active, listening for user speech
    ACTIVE_PROCESSING = "processing" # Processing user input with AI
    ACTIVE_SPEAKING = "speaking"     # AI is speaking response
    ACTIVE_WAITING = "waiting"       # Post-response, awaiting follow-up


class ConversationSession:
    """Manages a conversation session including state and history.

    A session starts when a wake word is detected and ends when:
    - The timeout expires without user speech
    - A stop word is detected
    - The session is explicitly deactivated
    """

    def __init__(self, timeout_seconds: float = 10.0, max_turns: int = 20):
        """Initialize a conversation session.

        Args:
            timeout_seconds: Seconds to wait for follow-up after AI response
            max_turns: Maximum conversation turns to retain in history
        """
        self._timeout_seconds = timeout_seconds
        self._timeout_start: Optional[float] = None
        self._state = SessionState.PASSIVE
        self.history = ConversationHistory(max_turns=max_turns)

    @property
    def state(self) -> SessionState:
        """Get current session state."""
        return self._state

    @state.setter
    def state(self, new_state: SessionState) -> None:
        """Set session state."""
        self._state = new_state

    @property
    def timeout_seconds(self) -> float:
        """Get the timeout duration."""
        return self._timeout_seconds

    @timeout_seconds.setter
    def timeout_seconds(self, value: float) -> None:
        """Set the timeout duration."""
        self._timeout_seconds = max(0.0, value)

    def activate(self) -> None:
        """Activate the session (switch from passive to active listening)."""
        self._state = SessionState.ACTIVE_LISTENING
        self._timeout_start = None

    def deactivate(self) -> None:
        """Deactivate the session and clear history."""
        self._state = SessionState.PASSIVE
        self._timeout_start = None
        self.history.clear()

    def set_listening(self) -> None:
        """Set state to active listening."""
        self._state = SessionState.ACTIVE_LISTENING
        self.cancel_timeout()

    def set_processing(self) -> None:
        """Set state to processing."""
        self._state = SessionState.ACTIVE_PROCESSING

    def set_speaking(self) -> None:
        """Set state to speaking."""
        self._state = SessionState.ACTIVE_SPEAKING

    def set_waiting(self) -> None:
        """Set state to waiting for follow-up and start timeout."""
        self._state = SessionState.ACTIVE_WAITING
        self.start_timeout()

    def start_timeout(self) -> None:
        """Start the post-response timeout timer."""
        self._timeout_start = time.time()

    def cancel_timeout(self) -> None:
        """Cancel the timeout timer (user started speaking)."""
        self._timeout_start = None

    def is_timeout_expired(self) -> bool:
        """Check if the timeout has expired."""
        if self._timeout_start is None:
            return False
        return time.time() - self._timeout_start >= self._timeout_seconds

    def time_remaining(self) -> float:
        """Get seconds remaining before timeout expires.

        Returns:
            Seconds remaining, or full timeout if not started
        """
        if self._timeout_start is None:
            return self._timeout_seconds
        elapsed = time.time() - self._timeout_start
        return max(0.0, self._timeout_seconds - elapsed)

    def reset_conversation(self) -> None:
        """Clear conversation history but stay in active mode."""
        self.history.clear()
        # Stay active - don't change state

    def is_active(self) -> bool:
        """Check if session is in any active state."""
        return self._state != SessionState.PASSIVE
