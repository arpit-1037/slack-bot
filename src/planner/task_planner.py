"""Deterministic task planning for Slack requests."""

from __future__ import annotations

from dataclasses import dataclass

from src.router.intent_router import IntentRouter, greeting_response
from src.tools.git_tool import is_git_action_query
from src.utils.helpers import clean_slack_mentions, get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class TaskPlan:
    """Execution plan for a cleaned Slack task."""

    original_task: str
    clean_task: str
    intent: str
    direct_response: str | None = None
    run_git_action: bool = False
    return_raw_git_diff: bool = False
    needs_git_context: bool = False
    needs_repository_context: bool = False
    needs_web_search: bool = False
    use_repository_debugger: bool = False


class TaskPlanner:
    """Turn incoming task text into explicit executor instructions."""

    def __init__(self, intent_router: IntentRouter | None = None) -> None:
        self.intent_router = intent_router or IntentRouter()

    def create_plan(self, task_text: str, request_id: str | None = None) -> TaskPlan:
        """Create a deterministic execution plan while preserving legacy routing."""
        clean = clean_slack_mentions(task_text)

        if not clean:
            log.info("request_id=%s empty task after Slack mention cleanup", request_id)
            return TaskPlan(
                original_task=task_text,
                clean_task=clean,
                intent="empty",
                direct_response="I did not receive a task. Please mention me with a task description.",
            )

        if is_git_action_query(clean):
            log.info("request_id=%s git action detected before intent routing", request_id)
            return TaskPlan(
                original_task=task_text,
                clean_task=clean,
                intent="git_action",
                run_git_action=True,
            )

        intent = self.intent_router.classify(clean)
        log.info("request_id=%s detected intent=%s", request_id, intent)

        if intent == "greeting":
            return TaskPlan(
                original_task=task_text,
                clean_task=clean,
                intent=intent,
                direct_response=greeting_response(),
            )

        if intent == "git_action":
            return TaskPlan(
                original_task=task_text,
                clean_task=clean,
                intent=intent,
                run_git_action=True,
            )

        if intent == "git":
            return TaskPlan(
                original_task=task_text,
                clean_task=clean,
                intent=intent,
                return_raw_git_diff=True,
            )

        return TaskPlan(
            original_task=task_text,
            clean_task=clean,
            intent=intent,
            needs_git_context=False,
            needs_repository_context=False,
            needs_web_search=intent == "web",
            use_repository_debugger=intent == "project_debug",
        )
