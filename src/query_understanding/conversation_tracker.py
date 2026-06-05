"""Slack thread conversation-state tracking."""

from __future__ import annotations

from src.memory.conversation_memory import ConversationMemory
from src.query_understanding.query_normalizer import normalize_query
from src.query_understanding.semantic_router import SemanticRouter
from src.query_understanding.topic_manager import TopicManager
from src.query_understanding.understanding_models import ConversationState, QueryAnalysis

_STATE_STORE: dict[str, ConversationState] = {}


class ConversationTracker:
    """Store and retrieve query-understanding state per Slack thread."""

    def __init__(
        self,
        memory: ConversationMemory | None = None,
        topic_manager: TopicManager | None = None,
        semantic_router: SemanticRouter | None = None,
    ) -> None:
        self.memory = memory or ConversationMemory()
        self.topic_manager = topic_manager or TopicManager()
        self.semantic_router = semantic_router or SemanticRouter()

    def get_state(
        self,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
    ) -> ConversationState:
        """Return the known state for a Slack thread, seeding from DB history."""
        key = self._thread_key(thread_ts, channel, slack_user)
        if key in _STATE_STORE:
            return _STATE_STORE[key]

        state = ConversationState(thread_key=key)
        for task_text, _solution in self.memory.get_history(thread_ts, channel, slack_user):
            state = self._state_from_prior_turn(state, task_text)
        _STATE_STORE[key] = state
        return state

    def update_state(
        self,
        analysis: QueryAnalysis,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
    ) -> ConversationState:
        """Persist the latest query analysis for a Slack thread."""
        key = self._thread_key(thread_ts, channel, slack_user)
        previous = _STATE_STORE.get(key) or ConversationState(thread_key=key)
        goal = analysis.resolved_query or analysis.normalized_query
        recent_goals = [goal] + [item for item in previous.recent_user_goals if item != goal]
        state = ConversationState(
            thread_key=key,
            active_topic=analysis.topic.active_topic,
            active_repository_task=goal if analysis.topic.active_topic in {"repository", "git"} else previous.active_repository_task,
            active_tool_name=analysis.selected_tool_name or previous.active_tool_name,
            recent_user_goals=recent_goals[:5],
            last_user_query=analysis.original_query,
            last_normalized_query=analysis.normalized_query,
            last_resolved_query=analysis.resolved_query,
            last_intent=analysis.selected_intent,
            last_tool_name=analysis.selected_tool_name or "",
        )
        _STATE_STORE[key] = state
        return state

    def _state_from_prior_turn(
        self,
        state: ConversationState,
        task_text: str,
    ) -> ConversationState:
        _original, normalized = normalize_query(task_text)
        route = self.semantic_router.route_query(normalized)
        topic = self.topic_manager.detect_topic(normalized, state)
        goal = normalized or task_text
        return ConversationState(
            thread_key=state.thread_key,
            active_topic=topic.active_topic,
            active_repository_task=goal if topic.active_topic in {"repository", "git"} else state.active_repository_task,
            active_tool_name=route.tool_name or state.active_tool_name,
            recent_user_goals=([goal] + [item for item in state.recent_user_goals if item != goal])[:5],
            last_user_query=task_text,
            last_normalized_query=normalized,
            last_resolved_query=normalized,
            last_intent=route.intent,
            last_tool_name=route.tool_name or "",
        )

    def _thread_key(
        self,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
    ) -> str:
        return "|".join(
            [
                channel or "no-channel",
                thread_ts or "no-thread",
                slack_user or "no-user",
            ]
        )
