"""Resolve short follow-up messages against thread state."""

from __future__ import annotations

import re

from src.query_understanding.understanding_models import ConversationState, FollowupResolution

_EXPLANATION_FOLLOWUPS = {"why", "why?", "how", "how?"}
_RETRY_FOLLOWUPS = {"try again", "again", "rerun", "run it again", "continue"}
_EXPAND_FOLLOWUPS = {"show more", "more", "expand", "details"}


class FollowupResolver:
    """Turn ambiguous short messages into context-bearing queries."""

    def resolve_followup(
        self,
        query: str,
        state: ConversationState | None = None,
    ) -> FollowupResolution:
        """Return a resolved query when the text depends on prior context."""
        normalized = re.sub(r"\s+", " ", (query or "").strip())
        lower = normalized.lower()
        if not normalized or state is None:
            return FollowupResolution(normalized, normalized)

        previous = state.last_resolved_query or state.last_normalized_query or state.active_repository_task
        if not previous:
            return FollowupResolution(normalized, normalized)

        if lower in _EXPLANATION_FOLLOWUPS:
            resolved = self._explain_previous(lower, previous)
            return FollowupResolution(
                original_query=normalized,
                resolved_query=resolved,
                is_followup=True,
                inherited_topic=state.active_topic,
                inherited_tool_name=state.active_tool_name or state.last_tool_name,
                reason="short explanation follow-up",
            )

        if lower in _RETRY_FOLLOWUPS or "after running this" in lower or "exact list" in lower:
            return FollowupResolution(
                original_query=normalized,
                resolved_query=previous,
                is_followup=True,
                inherited_topic=state.active_topic,
                inherited_tool_name=state.active_tool_name or state.last_tool_name,
                reason="retry previous goal",
            )

        if lower in _EXPAND_FOLLOWUPS:
            return FollowupResolution(
                original_query=normalized,
                resolved_query=f"{normalized} about {previous}",
                is_followup=True,
                inherited_topic=state.active_topic,
                inherited_tool_name=state.active_tool_name or state.last_tool_name,
                reason="expand previous goal",
            )

        if len(lower.split()) <= 3 and state.active_topic != "general":
            return FollowupResolution(
                original_query=normalized,
                resolved_query=f"{normalized} about {previous}",
                is_followup=True,
                inherited_topic=state.active_topic,
                inherited_tool_name=state.active_tool_name or state.last_tool_name,
                reason="short contextual follow-up",
            )

        return FollowupResolution(normalized, normalized)

    def _explain_previous(self, lower: str, previous: str) -> str:
        if lower.startswith("why"):
            return f"why couldn't you complete: {previous}"
        return f"how does this relate to: {previous}"


def resolve_followup(
    query: str,
    state: ConversationState | None = None,
) -> FollowupResolution:
    """Convenience wrapper for follow-up resolution."""
    return FollowupResolver().resolve_followup(query, state)
