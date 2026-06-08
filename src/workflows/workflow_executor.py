"""Execute controlled workflows through the existing execution engine."""

from __future__ import annotations

import time
from typing import Any

from src.execution.execution_engine import ExecutionEngine
from src.execution.execution_models import ExecutionSummary
from src.workflows.workflow_context import WorkflowContext
from src.workflows.workflow_models import Workflow, WorkflowExecution, WorkflowResult, WorkflowSummary
from src.workflows.workflow_prompts import WORKFLOW_NOTICE, WORKFLOW_REPORT_SECTIONS, WORKFLOW_TITLE
from src.utils.helpers import get_logger

log = get_logger(__name__)


class WorkflowExecutor:
    """Execute workflow steps by delegating investigation tasks to ExecutionEngine."""

    def __init__(self, execution_engine: ExecutionEngine | None = None) -> None:
        self.execution_engine = execution_engine or ExecutionEngine()

    def execute_workflow(
        self,
        workflow: Workflow,
        context: WorkflowContext,
        request_id: str | None = None,
    ) -> WorkflowSummary:
        """Execute a validated workflow and return a Slack-ready summary."""
        started = time.monotonic()
        executions: list[WorkflowExecution] = []
        completed: set[str] = set()
        failed: set[str] = set()

        for step in workflow.steps:
            if any(dependency in failed for dependency in step.dependencies):
                execution = WorkflowExecution(
                    step_id=step.id,
                    title=step.title,
                    status="skipped",
                    errors=["Skipped because a dependency failed."],
                )
                executions.append(execution)
                failed.add(step.id)
                continue
            missing = [dependency for dependency in step.dependencies if dependency not in completed]
            if missing:
                execution = WorkflowExecution(
                    step_id=step.id,
                    title=step.title,
                    status="skipped",
                    errors=[f"Skipped because dependencies are incomplete: {', '.join(missing)}"],
                )
                executions.append(execution)
                failed.add(step.id)
                continue

            step_start = time.monotonic()
            try:
                summary = self.execution_engine.execute_task(
                    step.task,
                    project_path=context.project_path,
                    request_id=request_id,
                )
                execution = self._execution_from_summary(step.id, step.title, summary, time.monotonic() - step_start)
            except Exception as error:
                execution = WorkflowExecution(
                    step_id=step.id,
                    title=step.title,
                    status="failure",
                    errors=[str(error)],
                    execution_time_seconds=round(time.monotonic() - step_start, 4),
                )
            executions.append(execution)
            context.record_execution(execution.as_dict())
            if execution.status in {"success", "partial"}:
                completed.add(step.id)
            else:
                failed.add(step.id)

        result = self._result(workflow, context.task, executions, time.monotonic() - started)
        report = self.generate_workflow_report(result)
        log.info(
            "workflow_id=%s workflow_type=%s status=%s duration=%.4f tools=%d failures=%d",
            workflow.id,
            workflow.workflow_type,
            result.status,
            result.execution_time_seconds,
            len(result.tools_used),
            len(result.failures),
        )
        return WorkflowSummary(result=result, report=report)

    def generate_workflow_report(self, result: WorkflowResult) -> str:
        """Generate a structured Slack report for a workflow result."""
        lines = [
            WORKFLOW_TITLE,
            WORKFLOW_NOTICE,
            "",
            f"*Selected Workflow:* {result.workflow_name}",
            f"*Workflow Type:* `{result.workflow_type}`",
            f"*Status:* {result.status}",
            "",
            WORKFLOW_REPORT_SECTIONS["summary"],
            f"- {result.task}",
        ]
        lines.extend(self._section(WORKFLOW_REPORT_SECTIONS["areas"], result.files_examined))
        lines.extend(self._section(WORKFLOW_REPORT_SECTIONS["tools"], result.tools_used))
        lines.extend(self._section(WORKFLOW_REPORT_SECTIONS["issues"], result.issues_found or ["No blocking issues found from read-only evidence."]))
        lines.extend(self._section(WORKFLOW_REPORT_SECTIONS["recommendations"], result.recommendations))
        if result.failures:
            lines.extend(self._section(WORKFLOW_REPORT_SECTIONS["failures"], result.failures))
        return "\n".join(lines)

    def _execution_from_summary(
        self,
        step_id: str,
        title: str,
        summary: ExecutionSummary,
        elapsed: float,
    ) -> WorkflowExecution:
        return WorkflowExecution(
            step_id=step_id,
            title=title,
            status=summary.status,
            execution_summary=summary.as_dict(),
            errors=list(summary.failures),
            tools_used=list(summary.tools_executed),
            files_examined=list(summary.files_examined),
            execution_time_seconds=round(elapsed, 4),
        )

    def _result(
        self,
        workflow: Workflow,
        task: str,
        executions: list[WorkflowExecution],
        elapsed: float,
    ) -> WorkflowResult:
        files = self._dedupe(path for execution in executions for path in execution.files_examined)
        tools = self._dedupe(tool for execution in executions for tool in execution.tools_used)
        failures = self._dedupe(error for execution in executions for error in execution.errors)
        issues = self._dedupe(
            issue
            for execution in executions
            for issue in execution.execution_summary.get("issues_found", [])
            if isinstance(execution.execution_summary, dict)
        )
        recommendations = self._dedupe(
            recommendation
            for execution in executions
            for recommendation in execution.execution_summary.get("recommendations", [])
            if isinstance(execution.execution_summary, dict)
        )
        if not recommendations:
            recommendations = ["Use the repository areas above for any follow-up investigation or fix."]

        status = self._status(executions, failures)
        return WorkflowResult(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            workflow_type=workflow.workflow_type,
            task=task,
            status=status,
            executions=executions,
            files_examined=files,
            tools_used=tools,
            issues_found=issues,
            recommendations=recommendations,
            failures=failures,
            execution_time_seconds=round(elapsed, 4),
            metadata=dict(workflow.metadata),
        )

    def _status(self, executions: list[WorkflowExecution], failures: list[str]) -> str:
        if not executions:
            return "skipped"
        if failures:
            return "partial" if any(execution.status == "success" for execution in executions) else "failure"
        if any(execution.status == "partial" for execution in executions):
            return "partial"
        return "success"

    def _section(self, title: str, items: list[str]) -> list[str]:
        lines = ["", title]
        if not items:
            lines.append("- None")
            return lines
        lines.extend(f"- {item}" for item in items[:12])
        return lines

    def _dedupe(self, items: Any) -> list[str]:
        result: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result


_default_executor: WorkflowExecutor | None = None


def default_workflow_executor() -> WorkflowExecutor:
    """Return a lazily created workflow executor."""
    global _default_executor
    if _default_executor is None:
        _default_executor = WorkflowExecutor()
    return _default_executor


def execute_workflow(
    workflow: Workflow,
    context: WorkflowContext,
    request_id: str | None = None,
) -> WorkflowSummary:
    """Execute a workflow using the default executor."""
    return default_workflow_executor().execute_workflow(workflow, context, request_id=request_id)


def generate_workflow_report(result: WorkflowResult) -> str:
    """Generate a workflow report using the default executor."""
    return default_workflow_executor().generate_workflow_report(result)
