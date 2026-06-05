"""Facade for safe read-only execution of planning engine outputs."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from src.execution.execution_context import ExecutionContext
from src.execution.execution_coordinator import ExecutionCoordinator
from src.execution.execution_models import (
    ExecutionPlan,
    ExecutionStep,
    ExecutionSummary,
    ToolExecutionRequest,
)
from src.planning.execution_models import Plan, PlanStep
from src.planning.planner import PlanningEngine
from src.utils.helpers import get_logger

log = get_logger(__name__)


class ExecutionEngine:
    """Build and execute read-only investigation workflows from structured plans."""

    def __init__(
        self,
        planning_engine: PlanningEngine | None = None,
        coordinator: ExecutionCoordinator | None = None,
    ) -> None:
        self.planning_engine = planning_engine or PlanningEngine()
        self.coordinator = coordinator or ExecutionCoordinator()

    def execute_task(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> ExecutionSummary:
        """Create a plan for a task, execute safe read-only steps, and summarize findings."""
        plan = self.planning_engine.create_plan(
            task,
            project_path=project_path,
            request_id=request_id,
        )
        return self.execute_plan(plan, project_path=project_path, request_id=request_id)

    def execute_plan(
        self,
        plan: Plan,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> ExecutionSummary:
        """Execute a structured planning plan with read-only registered tools."""
        execution_plan = self.build_execution_plan(plan, project_path=project_path)
        context = self.create_execution_context(execution_plan, plan, request_id=request_id)
        log.info(
            "request_id=%s execution_id=%s plan_id=%s source_plan_id=%s goal=%r",
            request_id,
            context.execution_id,
            execution_plan.id,
            execution_plan.source_plan_id,
            execution_plan.goal,
        )
        return self.coordinator.execute_plan(
            execution_plan,
            context,
            request_id=request_id,
        )

    def build_execution_plan(
        self,
        plan: Plan,
        project_path: str | None = None,
    ) -> ExecutionPlan:
        """Translate a planning plan into bounded read-only execution steps."""
        resolved_project_path = project_path or plan.context.project_path or "."
        source_plan_id = str(plan.metadata.get("plan_id") or self._stable_id("plan", plan.goal))
        execution_steps: list[ExecutionStep] = []
        seen_requests: set[str] = set()
        primary_files = [file.path for file in plan.context.repository_files[:4]]

        for index, source_step in enumerate(plan.steps, start=1):
            requests = self._requests_for_step(
                source_step,
                plan,
                primary_files=primary_files,
                seen_requests=seen_requests,
                step_index=index,
            )
            execution_steps.append(
                ExecutionStep(
                    id=source_step.id,
                    title=source_step.title,
                    description=source_step.description,
                    dependencies=list(source_step.dependencies),
                    tool_requests=requests,
                    source_plan_step_id=source_step.id,
                    expected_outcome=source_step.expected_outcome,
                )
            )

        return ExecutionPlan(
            id=self._stable_id("execution-plan", plan.goal, source_plan_id),
            goal=plan.goal,
            project_path=resolved_project_path,
            steps=execution_steps,
            source_plan_id=source_plan_id,
            metadata={
                "task_type": plan.analysis.task_type,
                "complexity": plan.analysis.complexity,
                "source_step_count": len(plan.steps),
                "read_only": True,
            },
        )

    def create_execution_context(
        self,
        execution_plan: ExecutionPlan,
        source_plan: Plan,
        request_id: str | None = None,
    ) -> ExecutionContext:
        """Create initial context from planning repository and git context."""
        execution_id = request_id or self._stable_id("execution", execution_plan.id)
        context = ExecutionContext(
            execution_id=execution_id,
            project_path=execution_plan.project_path,
            repository_context={
                "files": [file.path for file in source_plan.context.repository_files],
                "symbols": [symbol.name for symbol in source_plan.context.repository_symbols],
                "summary": source_plan.context.repository_summary,
            },
            git_context={
                "branch": source_plan.context.git.branch,
                "head_commit": source_plan.context.git.head_commit,
                "changed_files": source_plan.context.git.changed_files,
                "staged_files": source_plan.context.git.staged_files,
                "untracked_files": source_plan.context.git.untracked_files,
                "recent_commits": source_plan.context.git.recent_commits,
                "recent_changed_files": source_plan.context.git.recent_changed_files,
            },
            retrieval_context={
                "explanations": source_plan.context.retrieval_explanations,
                "debug_notes": source_plan.context.debug_notes,
            },
            metadata={
                "goal": execution_plan.goal,
                "plan_id": execution_plan.id,
                "source_plan_id": execution_plan.source_plan_id,
            },
        )
        return context

    def _requests_for_step(
        self,
        step: PlanStep,
        plan: Plan,
        primary_files: list[str],
        seen_requests: set[str],
        step_index: int,
    ) -> list[ToolExecutionRequest]:
        text = f"{plan.goal} {plan.analysis.task_type} {step.title} {step.description}".lower()
        requests: list[ToolExecutionRequest] = []
        queries = self._queries(plan.goal)

        if step_index == 1:
            self._add_request(
                requests,
                seen_requests,
                "repository.stats",
                {"largest_files_limit": 5},
                "Collect repository state before investigation.",
            )

        if self._matches(text, "locate", "identify", "collect", "retrieve", "find", "parse", "map", "source"):
            query_limit = 1 if primary_files else 2
            for query in queries[:query_limit]:
                self._add_request(
                    requests,
                    seen_requests,
                    "repository.file_search",
                    {"query": query, "search_content": True, "max_results": 10},
                    f"Search repository files for {query}.",
                )
            symbol_query = self._symbol_query(plan.goal)
            if symbol_query:
                self._add_request(
                    requests,
                    seen_requests,
                    "repository.symbol_search",
                    {"query": symbol_query, "kind": "any", "max_results": 10},
                    f"Search repository symbols for {symbol_query}.",
                )

        if primary_files and self._matches(text, "collect", "review", "inspect", "locate", "identify", "dependency", "evidence"):
            for file_path in primary_files[:3]:
                self._add_request(
                    requests,
                    seen_requests,
                    "system.file_reader",
                    {"path": file_path, "max_bytes": 12000},
                    f"Read retrieved file {file_path}.",
                )
            for file_path in primary_files[:2]:
                self._add_request(
                    requests,
                    seen_requests,
                    "repository.dependency_search",
                    {"file_path": file_path, "max_results": 8},
                    f"Inspect dependency links for {file_path}.",
                )

        if not primary_files and self._matches(text, "locate", "map", "source", "structure"):
            self._add_request(
                requests,
                seen_requests,
                "system.directory_tree",
                {"path": ".", "max_depth": 2, "max_entries": 120},
                "Inspect repository layout.",
            )

        if plan.analysis.requires_git_context or self._matches(text, "git", "recent", "changed", "history", "diff", "status", "regression"):
            self._add_request(
                requests,
                seen_requests,
                "git.status",
                {},
                "Inspect current working-tree state.",
            )
            self._add_request(
                requests,
                seen_requests,
                "git.log",
                {"limit": 5},
                "Review recent commit history.",
            )
            if self._matches(text, "diff", "changed", "recent", "regression", "review"):
                self._add_request(
                    requests,
                    seen_requests,
                    "git.diff",
                    {"max_chars": 30000},
                    "Inspect current repository diff.",
                )

        if self._matches(text, "test", "tests", "pytest", "unittest", "failing tests", "verification"):
            if self._matches(text, "test", "tests", "pytest", "unittest", "failing tests"):
                self._add_request(
                    requests,
                    seen_requests,
                    "validation.pytest",
                    {"timeout_seconds": 90},
                    "Run the repository test suite through the configured safe runner.",
                )
            if primary_files:
                self._add_request(
                    requests,
                    seen_requests,
                    "validation.syntax_check",
                    {"file_paths": primary_files[:4]},
                    "Check syntax for retrieved files.",
                )

        if self._matches(text, "lint", "style"):
            self._add_request(
                requests,
                seen_requests,
                "validation.lint",
                {"file_paths": primary_files[:4], "timeout_seconds": 60} if primary_files else {"timeout_seconds": 60},
                "Run configured lint checks.",
            )

        return requests

    def _add_request(
        self,
        requests: list[ToolExecutionRequest],
        seen_requests: set[str],
        tool_name: str,
        tool_input: dict[str, Any],
        reason: str,
        required: bool = False,
    ) -> None:
        key = self._request_key(tool_name, tool_input)
        if key in seen_requests:
            return
        seen_requests.add(key)
        requests.append(
            ToolExecutionRequest(
                tool_name=tool_name,
                tool_input=dict(tool_input),
                reason=reason,
                required=required,
            )
        )

    def _request_key(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        normalized = repr(sorted((str(key), repr(value)) for key, value in tool_input.items()))
        return f"{tool_name}:{normalized}"

    def _queries(self, goal: str) -> list[str]:
        keywords = self._keywords(goal)
        if not keywords:
            return ["repository"]
        priority = [
            keyword
            for keyword in keywords
            if keyword
            in {
                "slack",
                "event",
                "events",
                "auth",
                "authentication",
                "login",
                "jwt",
                "provider",
                "routing",
                "tests",
                "pytest",
                "config",
                "repository",
            }
        ]
        ordered = priority + [keyword for keyword in keywords if keyword not in priority]
        return ordered[:3]

    def _symbol_query(self, goal: str) -> str:
        keywords = self._keywords(goal)
        if not keywords:
            return ""
        for keyword in keywords:
            if keyword in {"handler", "router", "executor", "planner", "provider", "service", "auth", "login", "event"}:
                return keyword
        return max(keywords, key=len)

    def _keywords(self, value: str) -> list[str]:
        stop_words = {
            "the",
            "this",
            "that",
            "with",
            "from",
            "into",
            "why",
            "how",
            "what",
            "where",
            "check",
            "find",
            "review",
            "analyze",
            "investigate",
            "understand",
            "flow",
            "bug",
            "issue",
            "failing",
            "failed",
            "duplicate",
            "recent",
            "changes",
        }
        keywords: list[str] = []
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value.lower()):
            if token in stop_words or token in keywords:
                continue
            keywords.append(token)
        return keywords

    def _matches(self, text: str, *signals: str) -> bool:
        return any(signal in text for signal in signals)

    def _stable_id(self, prefix: str, *parts: str) -> str:
        digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"


_default_engine: ExecutionEngine | None = None


def default_execution_engine() -> ExecutionEngine:
    """Return a lazily created execution engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = ExecutionEngine()
    return _default_engine


def execute_plan(
    plan: Plan,
    project_path: str | None = None,
    request_id: str | None = None,
) -> ExecutionSummary:
    """Execute a planning plan using the default execution engine."""
    return default_execution_engine().execute_plan(
        plan,
        project_path=project_path,
        request_id=request_id,
    )


def execute_task(
    task: str,
    project_path: str | None = None,
    request_id: str | None = None,
) -> ExecutionSummary:
    """Create and execute a safe read-only plan using the default execution engine."""
    return default_execution_engine().execute_task(
        task,
        project_path=project_path,
        request_id=request_id,
    )
