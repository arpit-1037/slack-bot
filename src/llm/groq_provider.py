"""Groq chat provider adapter."""

from __future__ import annotations

import os

from src.llm.continuation_handler import CONTINUE_PROMPT, ContinuationHandler
from src.utils.helpers import ai_max_output_tokens


class GroqProvider:
    """Call Groq chat completions with continuation support."""

    name = "Groq"

    def __init__(self, continuation: ContinuationHandler | None = None) -> None:
        self.continuation = continuation or ContinuationHandler()

    def complete(self, messages: list[dict]) -> str:
        """Return the full Groq response, continuing on token-limit stops."""
        from groq import Groq

        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        working_messages = list(messages)
        parts = []

        while True:
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=working_messages,
                max_tokens=ai_max_output_tokens("GROQ_MAX_TOKENS"),
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            parts.append(content)

            if not content or not self.continuation.needs_continuation(getattr(choice, "finish_reason", None)):
                break

            working_messages.append({"role": "assistant", "content": content})
            working_messages.append({"role": "user", "content": CONTINUE_PROMPT})

        return "".join(parts)
