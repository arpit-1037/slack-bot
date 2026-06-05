"""Execute one safe read-only plan step."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from src.execution.execution_context import ExecutionContext
from src.execution.execution_models import ExecutionResult, ExecutionStep, ToolExecutionRequest
from src.execution.execution_validator import ExecutionValidator
from src.tools.base_tool import ToolResult
from src.tools.tool_executor import ToolExecutor
from src.utils.helpers import get_logger

log = get_logger(__name__)


class StepExecutor:
    """Execute individual execution steps through the existing tool executor."""

    def __init__(
        self,
        tool_executor: ToolExecutor | None = None,
        validator: ExecutionValidator | None = None,
    ) -> None:
        self.tool_executor = tool_executor or ToolExecutor()
        self.validator = validator or ExecutionValidator(registry=self.tool_executor.registry)

    def execute_step(
        self,
        step: ExecutionStep,
        context: ExecutionContext,
        request_id: str | None = None,
    ) -> ExecutionResult:
        """Execute a single step and return structured results."""
        started = time.monotonic()
        validation = self.validator.validate_step(step, context.project_path)
        if not validation.valid:
            log.warning(
                "request_id=%s execution_id=%s step_id=%s validation_failed errors=%s",
                request_id,
                context.execution_id,
                step.id,
                "; ".join(validation.errors),
            )
            return ExecutionResult(
                step_id=step.id,
                title=step.title,
                status="failure",
                errors=validation.errors,
                warnings=validation.warnings,
                execution_time_seconds=round(time.monotonic() - started, 4),
            )

        if not step.tool_requests:
            context.record_step(step.id, "success", 0)
            return ExecutionResult(
                step_id=step.id,
                title=step.title,
                status="success",
                warnings=validation.warnings,
                execution_time_seconds=round(time.monotonic() - started, 4),
                metadata={"message": "No tool calls were required for this step."},
            )

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for request in step.tool_requests:
            normalized = self._with_project_path(request, context.project_path)
            log.info(
                "request_id=%s execution_id=%s step_id=%s executing_tool=%s",
                request_id,
                context.execution_id,
                step.id,
                normalized.tool_name,
            )
            try:
                result = self.tool_executor.execute_tool(
                    normalized.tool_name,
                    normalized.tool_input,
                )
            except Exception as error:
                result = ToolResult.failure_result(
                    tool_name=normalized.tool_name,
                    error=str(error),
                )

            result_dict = result.as_dict()
            result_dict.setdefault("request_reason", normalized.reason)
            results.append(result_dict)
            context.record_tool_output(step.id, normalized.tool_name, result_dict)
            if not result.success and normalized.required:
                errors.append(f"{normalized.tool_name}: {result.error or 'tool failed'}")

        status = self._status(results, errors)
        context.record_step(step.id, status, len(results))
        return ExecutionResult(
            step_id=step.id,
            title=step.title,
            status=status,
            tool_results=results,
            errors=errors,
            warnings=validation.warnings,
            execution_time_seconds=round(time.monotonic() - started, 4),
        )

    def _with_project_path(self, request: ToolExecutionRequest, project_path: str) -> ToolExecutionRequest:
        tool_input = dict(request.tool_input)
        if request.tool_name.startswith("git."):
            tool_input.setdefault("repo_path", project_path)
        else:
            tool_input.setdefault("project_path", project_path)
        return replace(request, tool_input=tool_input)

    def _status(self, results: list[dict[str, Any]], errors: list[str]) -> str:
        if errors:
            return "failure"
        successes = sum(1 for result in results if result.get("success"))
        if successes == len(results):
            return "success"
        if successes:
            return "partial"
        return "failure"


_default_executor: StepExecutor | None = None


def default_step_executor() -> StepExecutor:
    """Return a lazily created step executor."""
    global _default_executor
    if _default_executor is None:
        _default_executor = StepExecutor()
    return _default_executor


def execute_step(
    step: ExecutionStep,
    context: ExecutionContext,
    request_id: str | None = None,
) -> ExecutionResult:
    """Execute one read-only step using the default step executor."""
    return default_step_executor().execute_step(step, context, request_id=request_id)
