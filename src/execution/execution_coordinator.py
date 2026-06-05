"""Coordinate safe read-only execution plan processing."""

from __future__ import annotations

import time

from src.execution.execution_context import ExecutionContext
from src.execution.execution_models import ExecutionPlan, ExecutionResult, ExecutionSummary
from src.execution.execution_prompts import EXECUTION_TITLE, VALIDATION_FAILED_NOTICE
from src.execution.execution_validator import ExecutionValidator
from src.execution.result_aggregator import ResultAggregator
from src.execution.step_executor import StepExecutor
from src.utils.helpers import get_logger

log = get_logger(__name__)


class ExecutionCoordinator:
    """Process execution steps in dependency order and collect results."""

    def __init__(
        self,
        step_executor: StepExecutor | None = None,
        validator: ExecutionValidator | None = None,
        aggregator: ResultAggregator | None = None,
    ) -> None:
        self.step_executor = step_executor or StepExecutor()
        self.validator = validator or ExecutionValidator(registry=self.step_executor.tool_executor.registry)
        self.aggregator = aggregator or ResultAggregator()

    def execute_plan(
        self,
        plan: ExecutionPlan,
        context: ExecutionContext,
        request_id: str | None = None,
    ) -> ExecutionSummary:
        """Execute a read-only plan and return aggregated findings."""
        started = time.monotonic()
        validation = self.validator.validate_execution_plan(plan)
        if not validation.valid:
            log.warning(
                "request_id=%s execution_id=%s plan_id=%s validation_failed errors=%s",
                request_id,
                context.execution_id,
                plan.id,
                "; ".join(validation.errors),
            )
            return self._validation_failure(plan, context, validation.errors, validation.warnings)

        log.info(
            "request_id=%s execution_id=%s plan_id=%s executing_steps=%d",
            request_id,
            context.execution_id,
            plan.id,
            len(plan.steps),
        )
        results: list[ExecutionResult] = []
        completed: set[str] = set()
        failed: set[str] = set()

        for step in plan.steps:
            missing_dependencies = [dependency for dependency in step.dependencies if dependency not in completed]
            if any(dependency in failed for dependency in step.dependencies):
                result = ExecutionResult(
                    step_id=step.id,
                    title=step.title,
                    status="skipped",
                    errors=["Skipped because a dependency failed."],
                )
                failed.add(step.id)
                results.append(result)
                continue
            if missing_dependencies:
                result = ExecutionResult(
                    step_id=step.id,
                    title=step.title,
                    status="skipped",
                    errors=[f"Skipped because dependencies are incomplete: {', '.join(missing_dependencies)}"],
                )
                failed.add(step.id)
                results.append(result)
                continue

            result = self.step_executor.execute_step(step, context, request_id=request_id)
            results.append(result)
            if result.status in {"success", "partial"}:
                completed.add(step.id)
            else:
                failed.add(step.id)

        summary = self.aggregator.aggregate_results(plan, results, context)
        summary.metadata["execution_time_seconds"] = round(time.monotonic() - started, 4)
        log.info(
            "request_id=%s execution_id=%s plan_id=%s status=%s tools=%d failures=%d execution_time=%.4f",
            request_id,
            context.execution_id,
            plan.id,
            summary.status,
            len(summary.tools_executed),
            len(summary.failures),
            summary.metadata["execution_time_seconds"],
        )
        return summary

    def _validation_failure(
        self,
        plan: ExecutionPlan,
        context: ExecutionContext,
        errors: list[str],
        warnings: list[str],
    ) -> ExecutionSummary:
        lines = [
            EXECUTION_TITLE,
            VALIDATION_FAILED_NOTICE,
            "",
            f"*Goal:* {plan.goal}",
            "",
            "*Errors:*",
            *(f"- {error}" for error in errors),
        ]
        if warnings:
            lines.extend(["", "*Warnings:*", *(f"- {warning}" for warning in warnings)])
        return ExecutionSummary(
            execution_id=context.execution_id,
            plan_id=plan.id,
            goal=plan.goal,
            status="failure",
            failures=errors,
            findings_report="\n".join(lines),
            metadata={"validation_warnings": warnings},
        )


_default_coordinator: ExecutionCoordinator | None = None


def default_execution_coordinator() -> ExecutionCoordinator:
    """Return a lazily created execution coordinator."""
    global _default_coordinator
    if _default_coordinator is None:
        _default_coordinator = ExecutionCoordinator()
    return _default_coordinator


def execute_plan(
    plan: ExecutionPlan,
    context: ExecutionContext,
    request_id: str | None = None,
) -> ExecutionSummary:
    """Execute a read-only plan using the default coordinator."""
    return default_execution_coordinator().execute_plan(plan, context, request_id=request_id)
