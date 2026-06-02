"""Small shared helpers used across orchestration modules."""

from __future__ import annotations

import logging
import os
import uuid

DEFAULT_AI_MAX_OUTPUT_TOKENS = 2048
DEFAULT_OPENAI_MAX_TOKENS = 2048


def get_logger(name: str) -> logging.Logger:
    """Return a module logger using the application's logging configuration."""
    return logging.getLogger(name)


def new_request_id() -> str:
    """Create a short request id for lifecycle tracing."""
    return uuid.uuid4().hex[:12]


def int_env(
    name: str,
    default: int,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    """Read an integer environment variable with bounds and fallback handling."""
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        value = default

    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def bool_env(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable with common truthy/falsy values."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def ai_max_output_tokens(
    provider_env: str,
    default: int = DEFAULT_AI_MAX_OUTPUT_TOKENS,
) -> int:
    """Resolve provider-specific token limits while preserving global fallback."""
    if os.getenv(provider_env):
        return int_env(provider_env, default)
    return int_env("AI_MAX_OUTPUT_TOKENS", default)


def clean_slack_mentions(task_text: str) -> str:
    """Remove Slack mention tokens from incoming text."""
    return " ".join(
        word for word in task_text.split()
        if not word.startswith("<@")
    ).strip()
