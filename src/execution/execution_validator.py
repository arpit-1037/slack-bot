"""Safety validation for read-only execution plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from src.execution.execution_models import (
    ExecutionPlan,
    ExecutionStep,
    ExecutionValidationResult,
    ToolExecutionRequest,
)
from src.tools.tool_registry import ToolRegistry, create_default_registry
from src.utils.helpers import int_env


@dataclass(frozen=True)
class ExecutionLimits:
    """Boundaries for safe read-only execution."""

    max_steps: int = 10
    max_tool_executions: int = 24
    max_tools_per_step: int = 5
    max_timeout_seconds: int = 120
    max_path_count: int = 30
    allowed_tools: set[str] = field(
        default_factory=lambda: {
            "git.status",
            "git.log",
            "git.diff",
            "git.branch",
            "repository.file_search",
            "repository.symbol_search",
            "repository.dependency_search",
            "repository.stats",
            "system.file_reader",
            "system.directory_tree",
            "system.file_metadata",
            "validation.pytest",
            "validation.lint",
            "validation.syntax_check",
        }
    )


class ExecutionValidator:
    """Validate execution plans before tools are invoked."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self.registry = registry or create_default_registry()
        self.limits = limits or ExecutionLimits(
            max_steps=int_env("EXECUTION_MAX_STEPS", 10, minimum=1, maximum=50),
            max_tool_executions=int_env("EXECUTION_MAX_TOOL_EXECUTIONS", 24, minimum=1, maximum=100),
            max_tools_per_step=int_env("EXECUTION_MAX_TOOLS_PER_STEP", 5, minimum=1, maximum=20),
            max_timeout_seconds=int_env("EXECUTION_MAX_TIMEOUT_SECONDS", 120, minimum=1, maximum=600),
        )

    def validate_execution_plan(self, plan: ExecutionPlan) -> ExecutionValidationResult:
        """Return validation errors and warnings for a full execution plan."""
        errors: list[str] = []
        warnings: list[str] = []
        root = Path(plan.project_path or ".").expanduser().resolve()

        if not plan.steps:
            errors.append("Execution plan has no steps.")
        if len(plan.steps) > self.limits.max_steps:
            errors.append(
                f"Execution plan has {len(plan.steps)} steps; limit is {self.limits.max_steps}."
            )

        step_ids = set()
        tool_count = 0
        for step in plan.steps:
            if step.id in step_ids:
                errors.append(f"Duplicate execution step id: {step.id}")
            step_ids.add(step.id)
            if len(step.tool_requests) > self.limits.max_tools_per_step:
                errors.append(
                    f"Step {step.id} requests {len(step.tool_requests)} tools; "
                    f"limit is {self.limits.max_tools_per_step}."
                )
            for dependency in step.dependencies:
                if dependency not in step_ids and dependency not in {item.id for item in plan.steps}:
                    errors.append(f"Step {step.id} depends on unknown step {dependency}.")
            for request in step.tool_requests:
                tool_count += 1
                self._validate_request(request, root, errors, warnings)

        if tool_count > self.limits.max_tool_executions:
            errors.append(
                f"Execution plan requests {tool_count} tools; "
                f"limit is {self.limits.max_tool_executions}."
            )

        return ExecutionValidationResult(valid=not errors, warnings=warnings, errors=errors)

    def validate_step(
        self,
        step: ExecutionStep,
        project_path: str,
    ) -> ExecutionValidationResult:
        """Validate one step in isolation."""
        isolated_step = ExecutionStep(
            id=step.id,
            title=step.title,
            description=step.description,
            dependencies=[],
            tool_requests=step.tool_requests,
            source_plan_step_id=step.source_plan_step_id,
            expected_outcome=step.expected_outcome,
        )
        plan = ExecutionPlan(
            id="single-step",
            goal=step.title,
            project_path=project_path,
            steps=[isolated_step],
        )
        return self.validate_execution_plan(plan)

    def _validate_request(
        self,
        request: ToolExecutionRequest,
        root: Path,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        tool_name = str(request.tool_name or "").strip()
        if tool_name not in self.limits.allowed_tools:
            errors.append(f"Tool is not allowed for read-only execution: {tool_name or 'unknown'}")
            return

        tool = self.registry.get_tool(tool_name)
        if tool is None:
            errors.append(f"Tool is not registered: {tool_name}")
            return

        metadata = tool.get_metadata()
        if not metadata.get("read_only", False):
            errors.append(f"Tool is not marked read-only: {tool_name}")

        tool_input = request.tool_input
        if not isinstance(tool_input, Mapping):
            errors.append(f"Tool input must be a mapping for {tool_name}.")
            return

        if tool_name == "validation.pytest" and "command" in tool_input:
            errors.append("Explicit test commands are not allowed in read-only execution plans.")

        timeout = tool_input.get("timeout_seconds")
        if timeout is not None:
            try:
                timeout_value = int(timeout)
            except (TypeError, ValueError):
                errors.append(f"timeout_seconds must be an integer for {tool_name}.")
            else:
                if timeout_value > self.limits.max_timeout_seconds:
                    errors.append(
                        f"timeout_seconds={timeout_value} exceeds limit "
                        f"{self.limits.max_timeout_seconds} for {tool_name}."
                    )

        paths = self._input_paths(tool_input)
        if len(paths) > self.limits.max_path_count:
            errors.append(f"Too many paths supplied to {tool_name}.")
        for path in paths:
            if not self._inside(root, path):
                errors.append(f"Path escapes repository boundary for {tool_name}: {path}")

        project_path = str(tool_input.get("project_path") or tool_input.get("repo_path") or "")
        if project_path and not self._same_or_inside(root, project_path):
            errors.append(f"Tool project path is outside execution root for {tool_name}: {project_path}")

        if tool_name in {"validation.pytest", "validation.lint"}:
            warnings.append(f"{tool_name} may execute repository-configured checks with timeouts.")

    def _input_paths(self, tool_input: Mapping[str, Any]) -> list[str]:
        paths: list[str] = []
        for field in ("path", "file_path"):
            value = tool_input.get(field)
            if isinstance(value, str) and value.strip():
                paths.append(value)
        value = tool_input.get("file_paths")
        if isinstance(value, list):
            paths.extend(item for item in value if isinstance(item, str) and item.strip())
        return paths

    def _inside(self, root: Path, path: str) -> bool:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            return False
        return True

    def _same_or_inside(self, root: Path, path: str) -> bool:
        candidate = Path(path).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return candidate == root
        return True


_default_validator: ExecutionValidator | None = None


def default_execution_validator() -> ExecutionValidator:
    """Return a lazily created execution validator."""
    global _default_validator
    if _default_validator is None:
        _default_validator = ExecutionValidator()
    return _default_validator


def validate_execution_plan(plan: ExecutionPlan) -> ExecutionValidationResult:
    """Validate a read-only execution plan using the default validator."""
    return default_execution_validator().validate_execution_plan(plan)
