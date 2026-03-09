"""Conversation context manager for multi-turn chat."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ConversationTurn:
    question: str
    answer: str
    timestamp: float = field(default_factory=time.time)


class ConversationManager:
    """Manage multi-turn conversation context in memory."""

    def __init__(self, max_turns: int = 10, ttl_minutes: int = 30):
        self._max_turns = max_turns
        self._ttl_seconds = ttl_minutes * 60
        self._conversations: dict[str, list[ConversationTurn]] = {}

    @staticmethod
    def _key(chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def get_context(self, chat_id: str, user_id: str) -> list[dict[str, str]]:
        """Get conversation history as LLM messages format."""
        key = self._key(chat_id, user_id)
        turns = self._conversations.get(key, [])

        # Filter expired turns
        now = time.time()
        turns = [t for t in turns if (now - t.timestamp) < self._ttl_seconds]
        self._conversations[key] = turns

        messages = []
        for turn in turns:
            messages.append({"role": "user", "content": turn.question})
            messages.append({"role": "assistant", "content": turn.answer})
        return messages

    def add_turn(self, chat_id: str, user_id: str, question: str, answer: str) -> None:
        """Add a conversation turn."""
        key = self._key(chat_id, user_id)
        if key not in self._conversations:
            self._conversations[key] = []

        self._conversations[key].append(ConversationTurn(question=question, answer=answer))

        # Trim to max_turns
        if len(self._conversations[key]) > self._max_turns:
            self._conversations[key] = self._conversations[key][-self._max_turns:]

    def clear(self, chat_id: str, user_id: str) -> None:
        """Clear conversation context."""
        key = self._key(chat_id, user_id)
        self._conversations.pop(key, None)

    def active_count(self) -> int:
        """Number of active conversations."""
        return len(self._conversations)
