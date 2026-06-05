"""Topic detection for repository-aware conversations."""

from __future__ import annotations

import re

from src.query_understanding.understanding_models import ConversationState, TopicState


_TOPIC_KEYWORDS = {
    "git": {
        "git",
        "branch",
        "branches",
        "commit",
        "commits",
        "diff",
        "history",
        "log",
        "merge",
        "rebase",
        "status",
        "staged",
        "unstaged",
        "untracked",
    },
    "repository": {
        "repository",
        "project",
        "codebase",
        "file",
        "files",
        "module",
        "class",
        "function",
        "method",
        "service",
        "handler",
        "controller",
        "route",
    },
    "testing": {"test", "tests", "pytest", "unittest", "coverage", "lint"},
    "debugging": {"bug", "error", "issue", "debug", "failing", "failed", "broken", "traceback"},
    "feature": {"feature", "implement", "add", "create", "build", "modify", "change"},
}


class TopicManager:
    """Detect and preserve active conversation topics."""

    def detect_topic(
        self,
        query: str,
        previous_state: ConversationState | None = None,
    ) -> TopicState:
        """Return the strongest topic for a normalized query."""
        tokens = set(re.findall(r"[a-z0-9_+-]+", (query or "").lower()))
        best_topic = "general"
        best_signals: list[str] = []
        for topic, keywords in _TOPIC_KEYWORDS.items():
            signals = sorted(tokens & keywords)
            if len(signals) > len(best_signals):
                best_topic = topic
                best_signals = signals

        previous_topic = previous_state.active_topic if previous_state else ""
        if best_topic == "general" and previous_topic:
            return TopicState(
                active_topic=previous_topic,
                previous_topic=previous_topic,
                confidence=0.42,
                signals=["inherited-topic"],
            )

        confidence = min(0.35 + len(best_signals) * 0.18, 0.95) if best_signals else 0.25
        return TopicState(
            active_topic=best_topic,
            previous_topic=previous_topic,
            confidence=round(confidence, 2),
            signals=best_signals,
        )

    def current_topic(self, state: ConversationState | None) -> str:
        """Return the current topic name from a conversation state."""
        return state.active_topic if state else "general"
