"""Tool wrapper around conversation memory."""

from __future__ import annotations

from src.memory.conversation_memory import ConversationMemory


class ConversationTool:
    """Expose conversation history to prompt building code."""

    def __init__(self, memory: ConversationMemory | None = None) -> None:
        self.memory = memory or ConversationMemory()

    def get_history(
        self,
        thread_ts: str | None,
        channel: str | None = None,
        slack_user: str | None = None,
    ) -> list[tuple[str, str]]:
        """Return prior solved turns for a Slack thread/user/channel."""
        return self.memory.get_history(thread_ts, channel, slack_user)
