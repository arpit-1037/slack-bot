"""Deterministic task planning for Slack requests."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.query_understanding import (
    ConversationTracker,
    FollowupResolver,
    SemanticRouter,
    TopicManager,
    normalize_query,
    score_intent_confidence,
)
from src.query_understanding.understanding_models import QueryAnalysis
from src.router.intent_router import IntentRouter, greeting_response, is_planning_query
from src.tools.git_tool import is_git_action_query
from src.utils.helpers import clean_slack_mentions, get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class TaskPlan:
    """Execution plan for a cleaned Slack task."""

    original_task: str
    clean_task: str
    intent: str
    normalized_task: str = ""
    direct_response: str | None = None
    run_git_action: bool = False
    return_raw_git_diff: bool = False
    selected_tool_name: str | None = None
    selected_tool_input: dict = field(default_factory=dict)
    needs_git_context: bool = False
    needs_repository_context: bool = False
    needs_web_search: bool = False
    use_planning_engine: bool = False
    use_execution_engine: bool = False
    use_workflow_engine: bool = False
    use_repository_debugger: bool = False
    use_repository_modifier: bool = False
    query_analysis: QueryAnalysis | None = None


class TaskPlanner:
    """Turn incoming task text into explicit executor instructions."""

    def __init__(
        self,
        intent_router: IntentRouter | None = None,
        semantic_router: SemanticRouter | None = None,
        topic_manager: TopicManager | None = None,
        followup_resolver: FollowupResolver | None = None,
        conversation_tracker: ConversationTracker | None = None,
        tool_confidence_threshold: float = 0.65,
    ) -> None:
        self.intent_router = intent_router or IntentRouter()
        self.semantic_router = semantic_router or SemanticRouter()
        self.topic_manager = topic_manager or TopicManager()
        self.followup_resolver = followup_resolver or FollowupResolver()
        self.conversation_tracker = conversation_tracker or ConversationTracker(
            topic_manager=self.topic_manager,
            semantic_router=self.semantic_router,
        )
        self.tool_confidence_threshold = tool_confidence_threshold

    def create_plan(
        self,
        task_text: str,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
    ) -> TaskPlan:
        """Create a deterministic execution plan while preserving legacy routing."""
        clean = clean_slack_mentions(task_text)
        analysis = self._analyze_query(
            clean,
            thread_ts=thread_ts,
            channel=channel,
            slack_user=slack_user,
            request_id=request_id,
        )
        routed_task = analysis.routing_query

        if not routed_task:
            log.info("request_id=%s empty task after Slack mention cleanup", request_id)
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent="empty",
                normalized_task=analysis.normalized_query,
                direct_response="I did not receive a task. Please mention me with a task description.",
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if is_planning_query(routed_task):
            log.info("request_id=%s planning request detected before execution routing", request_id)
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent="planning",
                normalized_task=analysis.normalized_query,
                use_planning_engine=True,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if is_git_action_query(routed_task):
            log.info("request_id=%s git action detected before intent routing", request_id)
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent="git_action",
                normalized_task=analysis.normalized_query,
                run_git_action=True,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        intent = self.intent_router.classify(routed_task)
        if intent == "project_execution":
            log.info("request_id=%s read-only execution request detected", request_id)
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent=intent,
                normalized_task=analysis.normalized_query,
                use_execution_engine=True,
                use_workflow_engine=True,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if analysis.selected_tool_name:
            log.info(
                "request_id=%s semantic tool route selected tool=%s confidence=%.2f",
                request_id,
                analysis.selected_tool_name,
                analysis.confidence,
            )
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent=analysis.selected_intent,
                normalized_task=analysis.normalized_query,
                selected_tool_name=analysis.selected_tool_name,
                selected_tool_input=analysis.selected_tool_input,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        log.info("request_id=%s detected intent=%s", request_id, intent)

        if self._is_contextual_explanation(analysis):
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent="general",
                normalized_task=analysis.normalized_query,
                needs_git_context=analysis.topic.active_topic == "git",
                needs_repository_context=analysis.topic.active_topic == "repository",
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if intent == "greeting":
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent=intent,
                normalized_task=analysis.normalized_query,
                direct_response=greeting_response(),
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if intent == "git_action":
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent=intent,
                normalized_task=analysis.normalized_query,
                run_git_action=True,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if intent == "planning":
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent=intent,
                normalized_task=analysis.normalized_query,
                use_planning_engine=True,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        if intent == "git":
            plan = TaskPlan(
                original_task=task_text,
                clean_task=routed_task,
                intent=intent,
                normalized_task=analysis.normalized_query,
                return_raw_git_diff=True,
                query_analysis=analysis,
            )
            self._store_plan_state(plan, thread_ts, channel, slack_user)
            return plan

        needs_git_context = (
            analysis.followup is not None
            and analysis.followup.is_followup
            and analysis.topic.active_topic == "git"
        )
        plan = TaskPlan(
            original_task=task_text,
            clean_task=routed_task,
            intent=intent,
            normalized_task=analysis.normalized_query,
            needs_git_context=needs_git_context,
            needs_repository_context=intent == "project_retrieval",
            needs_web_search=intent == "web",
            use_execution_engine=intent == "project_execution",
            use_workflow_engine=intent == "project_execution",
            use_repository_debugger=intent == "project_debug",
            use_repository_modifier=intent == "project_modify",
            query_analysis=analysis,
        )
        self._store_plan_state(plan, thread_ts, channel, slack_user)
        return plan

    def _analyze_query(
        self,
        clean: str,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
    ) -> QueryAnalysis:
        original, normalized = normalize_query(clean)
        state = self.conversation_tracker.get_state(thread_ts, channel, slack_user)
        followup = self.followup_resolver.resolve_followup(normalized, state)
        resolved = followup.resolved_query
        topic_state = None if followup.raw_git_command_detected else state
        topic = self.topic_manager.detect_topic(resolved, topic_state)
        semantic_result = self.semantic_router.route_query(resolved)
        classifier_intent = self.intent_router.classify(resolved) if resolved else "empty"
        intent_results = score_intent_confidence(
            resolved,
            classifier_intent=classifier_intent,
            semantic_result=semantic_result,
        )
        selected = intent_results[0] if intent_results else semantic_result
        selected_tool_name = (
            semantic_result.tool_name
            if semantic_result.confidence >= self.tool_confidence_threshold
            else None
        )
        selected_tool_input = semantic_result.tool_input if selected_tool_name else {}
        selected_intent = semantic_result.intent if selected_tool_name else classifier_intent
        confidence = semantic_result.confidence if selected_tool_name else selected.confidence

        log.info(
            "request_id=%s query understanding original=%r normalized=%r resolved=%r topic=%s intent=%s confidence=%.2f selected_tool=%s followup=%s raw_git_command_detected=%s",
            request_id,
            original,
            normalized,
            resolved,
            topic.active_topic,
            selected_intent,
            confidence,
            selected_tool_name or "none",
            followup.is_followup,
            followup.raw_git_command_detected,
        )
        return QueryAnalysis(
            original_query=original,
            normalized_query=normalized,
            resolved_query=resolved,
            topic=topic,
            intent_results=intent_results,
            selected_intent=selected_intent,
            confidence=confidence,
            selected_tool_name=selected_tool_name,
            selected_tool_input=selected_tool_input,
            followup=followup,
        )

    def _store_plan_state(
        self,
        plan: TaskPlan,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
    ) -> None:
        if plan.query_analysis is not None:
            self.conversation_tracker.update_state(
                plan.query_analysis,
                thread_ts=thread_ts,
                channel=channel,
                slack_user=slack_user,
            )

    def _is_contextual_explanation(self, analysis: QueryAnalysis) -> bool:
        followup = analysis.followup
        if followup is None or not followup.is_followup:
            return False
        return followup.original_query.lower().strip() in {"why", "why?", "how", "how?"}
