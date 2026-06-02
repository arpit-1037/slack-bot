"""Provider fallback routing for LLM calls."""

from __future__ import annotations

import os
from typing import Protocol

from src.llm.claude_provider import ClaudeProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.groq_provider import GroqProvider
from src.llm.openai_provider import OpenAIProvider
from src.utils.helpers import get_logger

log = get_logger(__name__)


class AIServiceUnavailableError(RuntimeError):
    """Raised when every configured AI provider fails for a task."""


class Provider(Protocol):
    """Protocol shared by provider adapters."""

    name: str

    def complete(self, messages: list[dict]) -> str:
        """Return the provider response."""


class ProviderRouter:
    """Try configured LLM providers in the existing fallback order."""

    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.providers = providers or self._default_providers()

    def _default_providers(self) -> list[Provider]:
        """Build provider order from env config or the standard fallback order."""
        provider_factories = {
            "groq": GroqProvider,
            "gemini": GeminiProvider,
            "claude": ClaudeProvider,
            "anthropic": ClaudeProvider,
            "openai": OpenAIProvider,
        }
        configured_order = [
            item.strip().lower()
            for item in os.getenv("AI_PROVIDER_ORDER", "").split(",")
            if item.strip()
        ]
        if configured_order:
            providers = []
            for provider_name in configured_order:
                factory = provider_factories.get(provider_name)
                if factory is None:
                    log.warning("Ignoring unknown AI provider in AI_PROVIDER_ORDER: %s", provider_name)
                    continue
                providers.append(factory())
            if providers:
                return providers

        return [
            GroqProvider(),
            GeminiProvider(),
            ClaudeProvider(),
            OpenAIProvider(),
        ]

    def complete(self, messages: list[dict], request_id: str | None = None) -> str:
        """Return the first successful provider response."""
        provider_errors = []

        for provider in self.providers:
            try:
                log.info("request_id=%s trying provider=%s", request_id, provider.name)
                result = provider.complete(messages)
                log.info("request_id=%s provider=%s responded", request_id, provider.name)
                return result
            except Exception as error:
                provider_errors.append(f"{provider.name}: {error}")
                log.warning(
                    "request_id=%s provider=%s failed: %s",
                    request_id, provider.name, error,
                )

        log.error("request_id=%s all AI providers failed: %s", request_id, "; ".join(provider_errors))
        raise AIServiceUnavailableError(
            "All configured AI providers failed. Check bot.log for quota, rate-limit, API-key, or payload-size errors."
        )
