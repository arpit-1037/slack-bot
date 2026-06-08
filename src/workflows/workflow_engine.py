"""Main orchestrator for controlled autonomous analysis workflows."""

from __future__ import annotations

import os
import time

from src.memory.repository_memory import RepositoryMemory
from src.workflows.workflow_builder import WorkflowBuilder
from src.workflows.workflow_context import WorkflowContext
from src.workflows.workflow_executor import WorkflowExecutor
from src.workflows.workflow_models import WorkflowSummary
from src.workflows.workflow_selector import WorkflowSelector
from src.workflows.workflow_validator import WorkflowValidator
from src.utils.helpers import get_logger

log = get_logger(__name__)


class WorkflowEngine:
    """Select, build, validate, and execute controlled analysis workflows."""

    def __init__(
        self,
        selector: WorkflowSelector | None = None,
        builder: WorkflowBuilder | None = None,
        validator: WorkflowValidator | None = None,
        executor: WorkflowExecutor | None = None,
        repository_memory: RepositoryMemory | None = None,
    ) -> None:
        self.selector = selector or WorkflowSelector()
        self.builder = builder or WorkflowBuilder(selector=self.selector)
        self.validator = validator or WorkflowValidator()
        self.executor = executor or WorkflowExecutor()
        self.repository_memory = repository_memory

    def run_workflow(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> WorkflowSummary:
        """Run the best predefined analysis workflow for a user task."""
        started = time.monotonic()
        resolved_project_path = os.path.abspath(os.path.expanduser(project_path or "."))
        selection = self.selector.select_workflow(task)
        workflow = self.builder.build_workflow(task, selection=selection)
        validation = self.validator.validate_workflow(workflow)
        context = self._context(workflow.id, task, resolved_project_path, selection, request_id=request_id)
        if not validation.valid:
            report = self._validation_failure_report(workflow.name, task, validation.errors, validation.warnings)
            from src.workflows.workflow_models import WorkflowResult

            return WorkflowSummary(
                result=WorkflowResult(
                    workflow_id=workflow.id,
                    workflow_name=workflow.name,
                    workflow_type=workflow.workflow_type,
                    task=task,
                    status="failure",
                    failures=validation.errors,
                    metadata={"validation_warnings": validation.warnings},
                ),
                report=report,
            )

        summary = self.executor.execute_workflow(workflow, context, request_id=request_id)
        summary.result.metadata["selection_confidence"] = selection.confidence
        summary.result.metadata["execution_duration_total_seconds"] = round(time.monotonic() - started, 4)
        log.info(
            "request_id=%s workflow_id=%s selected=%s confidence=%.4f status=%s findings=%d",
            request_id,
            workflow.id,
            workflow.workflow_type,
            selection.confidence,
            summary.result.status,
            len(summary.result.issues_found),
        )
        return summary

    def _context(
        self,
        workflow_id: str,
        task: str,
        project_path: str,
        selection: object,
        request_id: str | None = None,
    ) -> WorkflowContext:
        context = WorkflowContext(workflow_id=workflow_id, task=task, project_path=project_path)
        context.add_context("selection", getattr(selection, "workflow_type", ""), category="metadata")
        try:
            memory = self.repository_memory or RepositoryMemory(project_path)
            memory_result = memory.retrieve_memory(task, min_confidence=0.75)
            context.add_context("memory_hit", memory_result.hit, category="memory")
            context.add_context("best_confidence", memory_result.best_confidence, category="memory")
            context.add_context("summary", memory_result.summary, category="memory")
        except Exception as error:
            log.warning("request_id=%s workflow memory context skipped: %s", request_id, error)
        return context

    def _validation_failure_report(
        self,
        workflow_name: str,
        task: str,
        errors: list[str],
        warnings: list[str],
    ) -> str:
        lines = [
            "*Workflow Engine*",
            "Controlled workflow validation failed before execution.",
            "",
            f"*Selected Workflow:* {workflow_name}",
            f"*Task:* {task}",
            "",
            "*Errors:*",
            *(f"- {error}" for error in errors),
        ]
        if warnings:
            lines.extend(["", "*Warnings:*", *(f"- {warning}" for warning in warnings)])
        return "\n".join(lines)


_default_engine: WorkflowEngine | None = None


def default_workflow_engine() -> WorkflowEngine:
    """Return a lazily created workflow engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = WorkflowEngine()
    return _default_engine


def run_workflow(
    task: str,
    project_path: str | None = None,
    request_id: str | None = None,
) -> WorkflowSummary:
    """Run a controlled workflow using the default engine."""
    return default_workflow_engine().run_workflow(task, project_path=project_path, request_id=request_id)
