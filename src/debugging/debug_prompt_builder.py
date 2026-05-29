"""Focused prompt assembly for repository-aware debugging."""

from __future__ import annotations

from src.debugging.bug_context_builder import BugContext
from src.memory.conversation_memory import ConversationMemory
from src.prompts.system_prompt import SYSTEM_PROMPT
from src.utils.helpers import clean_slack_mentions, get_logger

log = get_logger(__name__)

DEBUG_SYSTEM_PROMPT = SYSTEM_PROMPT + """

DEBUGGING MODE:
- Diagnose the most likely cause first
- Use only the focused debugging context supplied
- Cite relevant files, functions, and line numbers when available
- Suggest the smallest safe fix
- Mention uncertainty only when the supplied context is insufficient"""


class DebugPromptBuilder:
    """Build concise provider-agnostic messages for debugging tasks."""

    def __init__(self, memory: ConversationMemory | None = None) -> None:
        self.memory = memory or ConversationMemory()

    def build_messages(
        self,
        task: str,
        bug_context: BugContext,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
    ) -> list[dict]:
        """Build a focused debugging prompt with conversation continuity."""
        messages: list[dict] = [{"role": "system", "content": DEBUG_SYSTEM_PROMPT}]
        history = self.memory.get_history(thread_ts, channel, slack_user)
        log.info(
            "request_id=%s debug history lookup thread_ts=%s channel=%s user=%s prior_turns=%d",
            request_id,
            thread_ts,
            channel,
            slack_user,
            len(history),
        )
        for past_task, past_solution in history:
            messages.append({"role": "user", "content": clean_slack_mentions(past_task)})
            messages.append({"role": "assistant", "content": past_solution})

        messages.append({
            "role": "user",
            "content": self._build_user_message(task, bug_context),
        })
        return messages

    def _build_user_message(self, task: str, bug_context: BugContext) -> str:
        """Build the debug-specific user message."""
        stacktrace = bug_context.stacktrace
        return f"""============================
DEBUGGING TASK
============================
{task}

============================
STACKTRACE SUMMARY
============================
error_type: {stacktrace.error_type or "not detected"}
error_message: {stacktrace.error_message or "not detected"}
files: {", ".join(stacktrace.files) or "none"}
lines: {", ".join(str(line) for line in stacktrace.lines) or "none"}
functions: {", ".join(stacktrace.functions) or "none"}

============================
REPOSITORY DEBUG CONTEXT
============================
{bug_context.format_context()}

============================
EXPECTED ANSWER
============================
Give the likely root cause, the specific files/functions involved, and the smallest practical fix."""
