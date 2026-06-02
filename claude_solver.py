"""Compatibility facade for the Slack AI task solver.

The orchestration is now split across src/planner, src/executor, src/tools,
src/prompts, and src/llm. This module preserves the legacy public API used by
app.py and any existing local scripts.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

from src.executor.task_executor import TaskExecutor
from src.llm.claude_provider import ClaudeProvider
from src.llm.continuation_handler import needs_continuation
from src.llm.gemini_provider import GeminiProvider
from src.llm.groq_provider import GroqProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.provider_router import AIServiceUnavailableError
from src.planner.task_planner import TaskPlanner
from src.prompts.prompt_builder import PromptBuilder
from src.prompts.system_prompt import SYSTEM_PROMPT
from src.prompts.templates import build_user_message
from src.repository.repository_scanner import read_codebase as scan_codebase
from src.router.intent_router import classify_intent, greeting_response
from src.tools.git_tool import (
    GitTool,
    extract_git_commands,
    git_repo_path,
    is_git_action_query,
    is_git_query,
)
from src.tools.web_search_tool import search_web
from src.utils.helpers import (
    DEFAULT_AI_MAX_OUTPUT_TOKENS,
    DEFAULT_OPENAI_MAX_TOKENS,
    ai_max_output_tokens,
    clean_slack_mentions,
    int_env,
    new_request_id,
)

load_dotenv()

log = logging.getLogger(__name__)


def _git_tool() -> GitTool:
    """Return a fresh git tool so env changes are reflected for legacy calls."""
    return GitTool()


def clean_text(task_text: str) -> str:
    """Remove Slack mention tokens from task text."""
    return clean_slack_mentions(task_text)


def run_git_command(args: list[str]) -> str:
    """Legacy wrapper for read-only git commands."""
    return _git_tool().run_command(args)


def run_git_action_command(args: list[str], timeout: int = 120) -> tuple[bool, str]:
    """Legacy wrapper for mutating git commands."""
    return _git_tool().run_action_command(args, timeout=timeout)


def has_git_changes(args: list[str]) -> bool:
    """Legacy wrapper for quiet git diff checks."""
    return _git_tool().has_git_changes(args)


def has_staged_changes() -> bool:
    """Legacy wrapper for staged change checks."""
    return _git_tool().has_staged_changes()


def has_worktree_changes() -> bool:
    """Legacy wrapper for worktree change checks."""
    return _git_tool().has_worktree_changes()


def format_git_result(args: list[str], ok: bool, output: str) -> str:
    """Legacy wrapper for Slack git command formatting."""
    return _git_tool().format_git_result(args, ok, output)


def is_git_repo() -> bool:
    """Legacy wrapper for repository detection."""
    return _git_tool().is_git_repo()


def get_default_diff_range() -> tuple[str, str]:
    """Legacy wrapper for recent diff range selection."""
    return _git_tool().get_default_diff_range()


def get_raw_diff() -> str:
    """Legacy wrapper for raw git diff responses."""
    return _git_tool().get_raw_diff()


def extract_commit_message(task: str) -> str:
    """Legacy wrapper for commit message extraction."""
    return _git_tool().extract_commit_message(task)


def suggest_commit_message() -> str:
    """Legacy wrapper for commit message suggestions."""
    return _git_tool().suggest_commit_message()


def command_has_commit_message(args: list[str]) -> bool:
    """Legacy wrapper for commit message flag detection."""
    return _git_tool().command_has_commit_message(args)


def normalize_git_command(args: list[str]) -> list[str]:
    """Legacy wrapper for git command normalization."""
    return _git_tool().normalize_git_command(args)


def run_git_commands(commands: list[list[str]]) -> str:
    """Legacy wrapper for batched git command execution."""
    return _git_tool().run_git_commands(commands)


def run_natural_git_action(task: str) -> str:
    """Legacy wrapper for natural-language git actions."""
    return _git_tool().run_natural_git_action(task)


def run_git_action(task: str) -> str:
    """Legacy wrapper for explicit or natural-language git actions."""
    return _git_tool().run_git_action(task)


def get_git_context() -> str:
    """Legacy wrapper for git context assembly."""
    return _git_tool().get_git_context()


def read_codebase(project_path: str | None = None) -> str:
    """Legacy wrapper for repository scanning."""
    return scan_codebase(project_path or git_repo_path())


def build_messages(
    task: str,
    thread_ts: str | None,
    channel: str | None,
    slack_user: str | None,
    intent: str,
    git_context: str,
    code_context: str,
    search_context: str,
) -> list[dict]:
    """Legacy wrapper for prompt assembly."""
    return PromptBuilder().build_messages(
        task,
        thread_ts,
        channel,
        slack_user,
        intent,
        git_context,
        code_context,
        search_context,
    )


def solve_with_groq(messages: list[dict]) -> str:
    """Legacy wrapper for Groq completion."""
    return GroqProvider().complete(messages)


def solve_with_gemini(messages: list[dict]) -> str:
    """Legacy wrapper for Gemini completion."""
    return GeminiProvider().complete(messages)


def solve_with_claude(messages: list[dict]) -> str:
    """Legacy wrapper for Claude completion."""
    return ClaudeProvider().complete(messages)


def solve_with_openai(messages: list[dict]) -> str:
    """Legacy wrapper for OpenAI completion."""
    return OpenAIProvider().complete(messages)


def solve_task(
    task_text: str,
    thread_ts: str | None = None,
    channel: str | None = None,
    slack_user: str | None = None,
    request_id: str | None = None,
) -> str:
    """Plan and execute a Slack task using the modular orchestration flow."""
    trace_id = request_id or new_request_id()
    log.info("request_id=%s solver started", trace_id)

    planner = TaskPlanner()
    executor = TaskExecutor()
    plan = planner.create_plan(task_text, request_id=trace_id)
    result = executor.execute(
        plan,
        thread_ts=thread_ts,
        channel=channel,
        slack_user=slack_user,
        request_id=trace_id,
    )

    log.info("request_id=%s solver completed intent=%s", trace_id, plan.intent)
    return result
