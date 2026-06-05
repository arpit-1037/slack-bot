"""Structured models for query understanding and conversation state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IntentResult:
    """Confidence-scored intent or tool-routing decision."""

    intent: str
    confidence: float
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TopicState:
    """Current topic inferred from a query or Slack thread."""

    active_topic: str = "general"
    previous_topic: str = ""
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConversationState:
    """Thread-scoped conversation state used to resolve short follow-ups."""

    thread_key: str
    active_topic: str = "general"
    active_repository_task: str = ""
    active_tool_name: str = ""
    recent_user_goals: list[str] = field(default_factory=list)
    last_user_query: str = ""
    last_normalized_query: str = ""
    last_resolved_query: str = ""
    last_intent: str = ""
    last_tool_name: str = ""


@dataclass(frozen=True)
class FollowupResolution:
    """Result of resolving a short message against conversation state."""

    original_query: str
    resolved_query: str
    is_followup: bool = False
    inherited_topic: str = ""
    inherited_tool_name: str = ""
    reason: str = ""


@dataclass(frozen=True)
class QueryAnalysis:
    """Complete understanding result for one incoming query."""

    original_query: str
    normalized_query: str
    resolved_query: str
    topic: TopicState
    intent_results: list[IntentResult] = field(default_factory=list)
    selected_intent: str = "general"
    confidence: float = 0.0
    selected_tool_name: str | None = None
    selected_tool_input: dict[str, Any] = field(default_factory=dict)
    followup: FollowupResolution | None = None

    @property
    def routing_query(self) -> str:
        """Return the query text that should be sent to legacy classifiers."""
        return self.resolved_query or self.normalized_query or self.original_query
