"""Conversation history access for Slack threads."""

from __future__ import annotations

from database import get_conversation_history


class ConversationMemory:
    """Load prior solved turns for the current Slack conversation."""

    def get_history(
        self,
        thread_ts: str | None,
        channel: str | None = None,
        slack_user: str | None = None,
    ) -> list[tuple[str, str]]:
        """Return prior task/solution pairs using the existing database behavior."""
        return get_conversation_history(thread_ts, channel, slack_user)
