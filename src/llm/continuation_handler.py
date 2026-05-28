"""Continuation detection shared by provider adapters."""

from __future__ import annotations

CONTINUE_PROMPT = "Continue exactly where you stopped. Do not restart, summarize, or repeat earlier content."


class ContinuationHandler:
    """Detect provider responses that stopped because of token limits."""

    def needs_continuation(self, finish_reason) -> bool:
        """Return True when the provider should be prompted to continue."""
        reason = str(finish_reason or "").lower()
        return reason in {"length", "max_tokens"} or "max_token" in reason


def needs_continuation(finish_reason) -> bool:
    """Compatibility helper for legacy callers."""
    return ContinuationHandler().needs_continuation(finish_reason)
