"""Gemini content generation provider adapter."""

from __future__ import annotations

import os

from src.llm.continuation_handler import CONTINUE_PROMPT, ContinuationHandler
from src.utils.helpers import ai_max_output_tokens

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class GeminiProvider:
    """Call Gemini with prompt flattening and continuation support."""

    name = "Gemini"

    def __init__(self, continuation: ContinuationHandler | None = None) -> None:
        self.continuation = continuation or ContinuationHandler()

    def complete(self, messages: list[dict]) -> str:
        """Return the full Gemini response, continuing on token-limit stops."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key())
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        convo_parts = []
        for message in messages:
            if message["role"] == "system":
                continue
            speaker = "User" if message["role"] == "user" else "Assistant"
            convo_parts.append(f"{speaker}: {message['content']}")
        convo = "\n\n".join(convo_parts)
        prompt = f"{system}\n\n{convo}" if system else convo
        parts = []
        current_prompt = prompt

        while True:
            response = client.models.generate_content(
                model=self._model(),
                contents=current_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=ai_max_output_tokens("GEMINI_MAX_OUTPUT_TOKENS"),
                ),
            )
            content = response.text or ""
            parts.append(content)

            candidate = response.candidates[0] if getattr(response, "candidates", None) else None
            finish_reason = getattr(candidate, "finish_reason", None)
            if not content or not self.continuation.needs_continuation(finish_reason):
                break

            current_prompt += f"\n\nAssistant: {content}\n\nUser: {CONTINUE_PROMPT}"

        return "".join(parts)

    def _api_key(self) -> str | None:
        """Return the configured Gemini API key."""
        key = os.getenv("GEMINI_API_KEY")
        return key.strip() if key else None

    def _model(self) -> str:
        """Return the configured Gemini model."""
        return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
