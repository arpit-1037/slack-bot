"""Anthropic Claude chat provider adapter."""

from __future__ import annotations

import os

from src.llm.continuation_handler import CONTINUE_PROMPT, ContinuationHandler
from src.utils.helpers import ai_max_output_tokens

DEFAULT_CLAUDE_MODEL = "claude-3-haiku-20240307"


class ClaudeProvider:
    """Call Anthropic Claude messages API with continuation support."""

    name = "Claude"

    def __init__(self, continuation: ContinuationHandler | None = None) -> None:
        self.continuation = continuation or ContinuationHandler()

    def complete(self, messages: list[dict]) -> str:
        """Return the full Claude response, continuing on token-limit stops."""
        from anthropic import Anthropic

        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY or CLAUDE_API_KEY.")

        client = Anthropic(api_key=api_key)
        system, working_messages = self._prepare_messages(messages)
        parts = []

        while True:
            request = {
                "model": os.getenv("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
                "messages": working_messages,
                "max_tokens": self._max_tokens(),
            }
            if system:
                request["system"] = system

            response = client.messages.create(**request)
            content = self._response_text(response)
            parts.append(content)

            if not content or not self.continuation.needs_continuation(getattr(response, "stop_reason", None)):
                break

            working_messages.append({"role": "assistant", "content": content})
            working_messages.append({"role": "user", "content": CONTINUE_PROMPT})

        return "".join(parts)

    def _api_key(self) -> str:
        """Return the configured Anthropic key, accepting Claude as a user-friendly alias."""
        return (
            os.getenv("ANTHROPIC_API_KEY", "").strip()
            or os.getenv("CLAUDE_API_KEY", "").strip()
        )

    def _max_tokens(self) -> int:
        """Return Claude output-token limit with provider-specific env fallback."""
        if os.getenv("ANTHROPIC_MAX_TOKENS"):
            return ai_max_output_tokens("ANTHROPIC_MAX_TOKENS")
        return ai_max_output_tokens("CLAUDE_MAX_TOKENS")

    def _prepare_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Split system prompt from user/assistant messages for Anthropic."""
        system_parts = []
        claude_messages = []
        for message in messages:
            role = message.get("role", "user")
            content = str(message.get("content", ""))
            if role == "system":
                system_parts.append(content)
                continue
            claude_messages.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            })
        return "\n\n".join(part for part in system_parts if part), claude_messages

    def _response_text(self, response) -> str:
        """Extract text blocks from an Anthropic message response."""
        chunks = []
        for block in getattr(response, "content", []) or []:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                chunks.append(text)
        return "".join(chunks)
