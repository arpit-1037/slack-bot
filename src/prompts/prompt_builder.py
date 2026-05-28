"""Conversation-aware prompt assembly."""

from __future__ import annotations

from src.memory.conversation_memory import ConversationMemory
from src.prompts.system_prompt import SYSTEM_PROMPT
from src.prompts.templates import build_user_message
from src.utils.helpers import clean_slack_mentions, get_logger

log = get_logger(__name__)


class PromptBuilder:
    """Build provider-agnostic chat messages for a task."""

    def __init__(self, memory: ConversationMemory | None = None) -> None:
        self.memory = memory or ConversationMemory()

    def build_messages(
        self,
        task: str,
        thread_ts: str | None,
        channel: str | None,
        slack_user: str | None,
        intent: str,
        git_context: str,
        code_context: str,
        search_context: str,
        request_id: str | None = None,
    ) -> list[dict]:
        """Build system, history, and current task messages."""
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        history = self.memory.get_history(thread_ts, channel, slack_user)
        log.info(
            "request_id=%s history lookup: thread_ts=%s channel=%s user=%s prior_turns=%d",
            request_id, thread_ts, channel, slack_user, len(history),
        )
        for past_task, past_solution in history:
            messages.append({"role": "user", "content": clean_slack_mentions(past_task)})
            messages.append({"role": "assistant", "content": past_solution})
        messages.append({
            "role": "user",
            "content": build_user_message(task, intent, git_context, code_context, search_context),
        })
        return messages
