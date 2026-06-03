"""Main orchestrator for thinking-only planning."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from src.debugging.stacktrace_parser import StacktraceParser
from src.hybrid_retrieval.hybrid_retriever import HybridRetriever
from src.planning.execution_models import (
    GitPlanningContext,
    Plan,
    PlanningContext,
    PlanningFileContext,
    PlanningSymbolContext,
)
from src.planning.plan_generator import PlanGenerator
from src.planning.plan_validator import PlanValidator
from src.planning.task_analyzer import TaskAnalyzer
from src.retrieval.retrieval_engine import RepositoryRetrievalEngine
from src.router.intent_router import is_planning_query
from src.tools.git_tool import GitTool
from src.utils.helpers import clean_slack_mentions, get_logger

log = get_logger(__name__)


class PlanningEngine:
    """Coordinate task analysis, context attachment, plan generation, and validation."""

    def __init__(
        self,
        task_analyzer: TaskAnalyzer | None = None,
        plan_generator: PlanGenerator | None = None,
        plan_validator: PlanValidator | None = None,
        retrieval_engine: RepositoryRetrievalEngine | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        git_tool: GitTool | None = None,
        stacktrace_parser: StacktraceParser | None = None,
    ) -> None:
        self.task_analyzer = task_analyzer or TaskAnalyzer()
        self.plan_generator = plan_generator or PlanGenerator()
        self.plan_validator = plan_validator or PlanValidator()
        self.retrieval_engine = retrieval_engine or RepositoryRetrievalEngine()
        self.hybrid_retriever = hybrid_retriever
        self.git_tool = git_tool or GitTool()
        self.stacktrace_parser = stacktrace_parser or StacktraceParser()

    def create_plan(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> Plan:
        """Create, contextualize, and validate a structured plan without executing it."""
        clean_task = clean_slack_mentions(task)
        if is_planning_query(clean_task):
            clean_task = self._strip_planning_request(clean_task)

        initial_analysis = self.task_analyzer.analyze_task(clean_task)
        resolved_path = os.path.abspath(os.path.expanduser(project_path or self.git_tool.repo_path))
        context = PlanningContext(task=clean_task, project_path=resolved_path)

        if initial_analysis.requires_repository_context:
            context = self.attach_repository_context(context, request_id=request_id)
        if initial_analysis.requires_git_context:
            context = self.attach_git_context(context)
        if initial_analysis.task_type in {"Bug Fix", "Investigation"}:
            context = self.attach_debug_context(context)

        analysis = self.task_analyzer.analyze_task(clean_task, context=context)
        plan = self.plan_generator.generate_plan(clean_task, analysis, context=context)
        validation = self.plan_validator.validate_plan(plan)
        plan = replace(plan, validation=validation)
        plan = replace(plan, explanation=self.explain_plan(plan))

        log.info(
            "request_id=%s planning completed type=%s complexity=%s steps=%d valid=%s",
            request_id,
            plan.analysis.task_type,
            plan.analysis.complexity,
            len(plan.steps),
            plan.validation.valid,
        )
        return plan

    def generate_execution_plan(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> Plan:
        """Return a plan-only execution strategy; this method never executes the plan."""
        return self.create_plan(task, project_path=project_path, request_id=request_id)

    def attach_repository_context(
        self,
        context: PlanningContext,
        request_id: str | None = None,
    ) -> PlanningContext:
        """Attach repository and hybrid retrieval context to the planning context."""
        try:
            if self.hybrid_retriever is not None:
                result: Any = self.hybrid_retriever.retrieve(
                    project_path=context.project_path,
                    query=context.task,
                    request_id=request_id,
                )
                files = result.files
                symbols = result.symbols
                repository_summary = result.context.repository_summary
                explanations = list(getattr(result, "explanations", []))
            else:
                result = self.retrieval_engine.retrieve_context(
                    project_path=context.project_path,
                    query=context.task,
                    request_id=request_id,
                )
                files = result.files
                symbols = result.symbols
                repository_summary = result.context.repository_summary
                explanations = list(result.context.ranking_decisions)
        except Exception as error:
            log.warning("request_id=%s planning repository context skipped: %s", request_id, error)
            return replace(
                context,
                retrieval_explanations=[f"Repository context unavailable: {error}"],
            )

        return replace(
            context,
            repository_files=[
                PlanningFileContext(
                    path=file.path,
                    score=file.score,
                    reasons=list(file.reasons),
                    dependencies=list(file.dependencies),
                    dependents=list(file.dependents),
                )
                for file in files
            ],
            repository_symbols=[
                PlanningSymbolContext(
                    name=symbol.name,
                    kind=symbol.kind,
                    file_path=symbol.file_path,
                    line_start=symbol.line_start,
                    line_end=symbol.line_end,
                    reasons=list(symbol.reasons),
                )
                for symbol in symbols
            ],
            repository_summary=repository_summary,
            retrieval_explanations=explanations,
        )

    def attach_git_context(self, context: PlanningContext) -> PlanningContext:
        """Attach read-only git state and recent history to the planning context."""
        if not self.git_tool.is_git_repo():
            return replace(context, git=GitPlanningContext())

        git_context = GitPlanningContext(
            branch=self.git_tool.run_command(["branch", "--show-current"]),
            head_commit=self.git_tool.run_command(["rev-parse", "HEAD"]),
            changed_files=self._lines(self.git_tool.run_command(["diff", "--name-only"])),
            staged_files=self._lines(self.git_tool.run_command(["diff", "--cached", "--name-only"])),
            untracked_files=self._lines(
                self.git_tool.run_command(["ls-files", "--others", "--exclude-standard"])
            ),
            recent_commits=self._lines(self.git_tool.run_command(["log", "--oneline", "-5"])),
            recent_changed_files=self._recent_changed_files(),
        )
        return replace(context, git=git_context)

    def attach_debug_context(self, context: PlanningContext) -> PlanningContext:
        """Attach stacktrace-derived debugging notes without invoking the debugger LLM path."""
        stacktrace = self.stacktrace_parser.parse(context.task)
        notes: list[str] = []
        if stacktrace.error_type:
            notes.append(f"error type: {stacktrace.error_type}")
        if stacktrace.error_message:
            notes.append(f"error message: {stacktrace.error_message}")
        if stacktrace.frames:
            notes.append(
                "stack frames: "
                + ", ".join(
                    f"{frame.filename}:{frame.line_number}" for frame in stacktrace.frames[:4]
                )
            )
        return replace(context, debug_notes=notes)

    def explain_plan(self, plan: Plan) -> list[str]:
        """Generate a concise explanation for why the plan was shaped this way."""
        explanation = list(plan.explanation)
        validation_summary = (
            "Plan validation passed."
            if plan.validation.valid
            else "Plan validation found errors that must be addressed before use."
        )
        explanation.append(validation_summary)
        if plan.validation.warnings:
            explanation.append("Validation warnings: " + "; ".join(plan.validation.warnings))
        return explanation

    def _strip_planning_request(self, task: str) -> str:
        """Remove leading planning words while preserving the actual goal."""
        return self.task_analyzer._normalize_task(task)

    def _lines(self, value: str) -> list[str]:
        """Split command output into non-empty lines."""
        return [line.strip() for line in value.splitlines() if line.strip()]

    def _recent_changed_files(self) -> list[str]:
        """Return unique files touched by recent commits using read-only git history."""
        output = self.git_tool.run_command(["log", "--name-only", "--pretty=format:", "--max-count=5"])
        files = []
        for line in self._lines(output):
            if line not in files:
                files.append(line)
        return files


_default_engine: PlanningEngine | None = None


def default_planning_engine() -> PlanningEngine:
    """Return a lazily created planning engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = PlanningEngine()
    return _default_engine


def create_plan(
    task: str,
    project_path: str | None = None,
    request_id: str | None = None,
) -> Plan:
    """Create a validated structured plan using the default engine."""
    return default_planning_engine().create_plan(task, project_path=project_path, request_id=request_id)


def generate_execution_plan(
    task: str,
    project_path: str | None = None,
    request_id: str | None = None,
) -> Plan:
    """Create a plan-only execution strategy using the default engine."""
    return default_planning_engine().generate_execution_plan(
        task,
        project_path=project_path,
        request_id=request_id,
    )
