"""Sentence buffer for streaming text processing."""

import re
import logging
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)

# Sentence-ending patterns
SENTENCE_ENDINGS = re.compile(r'[.!?]+[\s\n]+|[.!?]+$|\n\n+')

# Minimum sentence length to emit
MIN_SENTENCE_LENGTH = 10


class SentenceBuffer:
    """
    Buffers streaming text and yields complete sentences.

    Handles edge cases like:
    - Abbreviations (Mr., Dr., etc.)
    - Decimal numbers (3.14)
    - Incomplete sentences at end of stream
    """

    # Common abbreviations that shouldn't end sentences
    ABBREVIATIONS = {
        'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'vs', 'etc', 'inc',
        'ltd', 'co', 'corp', 'st', 'ave', 'blvd', 'rd', 'no', 'vol', 'rev',
        'hr', 'herr', 'frau', 'bzw', 'ca', 'z.b', 'u.a', 'd.h'
    }

    def __init__(self, min_length: int = MIN_SENTENCE_LENGTH):
        """
        Initialize sentence buffer.

        Args:
            min_length: Minimum characters before emitting a sentence
        """
        self.buffer = ""
        self.min_length = min_length

    def add(self, text: str) -> List[str]:
        """
        Add text to buffer and return any complete sentences.

        Args:
            text: New text chunk to add

        Returns:
            List of complete sentences (may be empty)
        """
        self.buffer += text
        return self._extract_sentences()

    def _extract_sentences(self) -> List[str]:
        """Extract complete sentences from buffer."""
        sentences = []

        while True:
            match = SENTENCE_ENDINGS.search(self.buffer)

            if not match:
                break

            end_pos = match.end()
            potential_sentence = self.buffer[:end_pos].strip()

            # Check if this is a false positive (abbreviation, number)
            if self._is_false_boundary(potential_sentence):
                # Look for next boundary
                rest = self.buffer[end_pos:]
                next_match = SENTENCE_ENDINGS.search(rest)
                if next_match:
                    end_pos = end_pos + next_match.end()
                    potential_sentence = self.buffer[:end_pos].strip()
                else:
                    break

            # Check minimum length
            if len(potential_sentence) >= self.min_length:
                sentences.append(potential_sentence)
                self.buffer = self.buffer[end_pos:].lstrip()
            else:
                break

        return sentences

    def _is_false_boundary(self, text: str) -> bool:
        """Check if the period is likely not a sentence ending."""
        if not text:
            return True

        # Check for abbreviations
        words = text.lower().split()
        if words:
            last_word = words[-1].rstrip('.!?')
            if last_word in self.ABBREVIATIONS:
                return True

        # Check for decimal numbers (e.g., "3.14")
        if re.search(r'\d\.\d*$', text.rstrip('.!?')):
            return True

        return False

    def flush(self) -> Optional[str]:
        """
        Get any remaining text in buffer.

        Call this when the stream ends to get the final incomplete sentence.

        Returns:
            Remaining text or None if buffer is empty
        """
        remaining = self.buffer.strip()
        self.buffer = ""
        return remaining if remaining else None

    def clear(self) -> None:
        """Clear the buffer."""
        self.buffer = ""


def stream_sentences(
    text_iterator: Iterator[str],
    min_length: int = MIN_SENTENCE_LENGTH
) -> Iterator[str]:
    """
    Convenience function to convert a text chunk stream to a sentence stream.

    Args:
        text_iterator: Iterator yielding text chunks
        min_length: Minimum sentence length

    Yields:
        Complete sentences
    """
    buffer = SentenceBuffer(min_length)

    for chunk in text_iterator:
        sentences = buffer.add(chunk)
        for sentence in sentences:
            yield sentence

    # Don't forget the final sentence
    final = buffer.flush()
    if final:
        yield final
