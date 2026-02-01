"""Conversation history management for multi-turn conversations."""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ConversationMessage:
    """A single message in the conversation history."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)


class ConversationHistory:
    """In-memory conversation history for a single active session.

    Maintains a list of user and assistant messages within an active session.
    History is cleared when returning to passive mode - no persistence across sessions.
    """

    def __init__(self, max_turns: int = 20):
        """Initialize conversation history.

        Args:
            max_turns: Maximum number of turns (user+assistant pairs) to retain.
        """
        self._messages: List[ConversationMessage] = []
        self.max_turns = max_turns

    def add_user_message(self, content: str) -> None:
        """Add a user message to the history."""
        if not content.strip():
            return
        self._messages.append(ConversationMessage(role="user", content=content.strip()))
        self._prune_if_needed()

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the history."""
        if not content.strip():
            return
        self._messages.append(ConversationMessage(role="assistant", content=content.strip()))
        self._prune_if_needed()

    def get_messages(self) -> List[ConversationMessage]:
        """Get all messages in the history."""
        return list(self._messages)

    def get_formatted_history(self, format: str = "openai") -> List[dict]:
        """Get history formatted for AI provider APIs.

        Args:
            format: Output format - "openai" for OpenAI/Grok API format

        Returns:
            List of message dicts in the requested format
        """
        if format == "openai":
            return [{"role": msg.role, "content": msg.content} for msg in self._messages]
        else:
            raise ValueError(f"Unknown format: {format}")

    def get_formatted_text(self, max_messages: int = 6) -> str:
        """Get history as human-readable text for voice context injection.

        Args:
            max_messages: Maximum number of recent messages to include

        Returns:
            Formatted text summary of recent conversation
        """
        recent = self._messages[-max_messages:] if max_messages else self._messages
        if not recent:
            return ""

        lines = []
        for msg in recent:
            role_label = "User" if msg.role == "user" else "Dodi"
            lines.append(f"{role_label}: {msg.content}")

        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all messages from history."""
        self._messages.clear()

    def is_empty(self) -> bool:
        """Check if history has no messages."""
        return len(self._messages) == 0

    def get_turn_count(self) -> int:
        """Get the number of complete turns (user+assistant pairs)."""
        user_count = sum(1 for m in self._messages if m.role == "user")
        assistant_count = sum(1 for m in self._messages if m.role == "assistant")
        return min(user_count, assistant_count)

    def _prune_if_needed(self) -> None:
        """Remove oldest messages if we exceed max_turns."""
        # Count turns (pairs of user+assistant messages)
        turn_count = self.get_turn_count()

        if turn_count > self.max_turns:
            # Remove oldest pair(s)
            messages_to_remove = (turn_count - self.max_turns) * 2
            self._messages = self._messages[messages_to_remove:]
