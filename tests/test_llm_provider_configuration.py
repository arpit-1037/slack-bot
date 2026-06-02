"""Testable examples for LLM provider configuration."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.llm.claude_provider import ClaudeProvider
from src.llm.provider_router import ProviderRouter


class ClaudeProviderConfigurationTest(unittest.TestCase):
    """Examples for Claude environment key handling and prompt conversion."""

    def test_claude_accepts_anthropic_or_claude_key_env_names(self) -> None:
        provider = ClaudeProvider()

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "primary", "CLAUDE_API_KEY": "alias"}, clear=False):
            self.assertEqual(provider._api_key(), "primary")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "CLAUDE_API_KEY": "alias"}, clear=False):
            self.assertEqual(provider._api_key(), "alias")

    def test_claude_splits_system_prompt_from_messages(self) -> None:
        system, messages = ClaudeProvider()._prepare_messages(
            [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ]
        )

        self.assertEqual(system, "Be brief.")
        self.assertEqual(messages, [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ])


class ProviderRouterConfigurationTest(unittest.TestCase):
    """Examples for default provider fallback order."""

    def test_default_router_includes_claude_before_openai(self) -> None:
        with patch.dict(os.environ, {"AI_PROVIDER_ORDER": ""}, clear=False):
            provider_names = [provider.name for provider in ProviderRouter().providers]

        self.assertEqual(provider_names, ["Groq", "Gemini", "Claude", "OpenAI"])

    def test_provider_order_can_use_gemini_only(self) -> None:
        with patch.dict(os.environ, {"AI_PROVIDER_ORDER": "gemini"}, clear=False):
            provider_names = [provider.name for provider in ProviderRouter().providers]

        self.assertEqual(provider_names, ["Gemini"])


if __name__ == "__main__":
    unittest.main()
