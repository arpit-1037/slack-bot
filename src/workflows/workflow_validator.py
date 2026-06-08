"""Safety validation for controlled autonomous workflows."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.workflows.workflow_models import Workflow, WorkflowValidationResult


@dataclass(frozen=True)
class WorkflowLimits:
    """Execution limits for workflow orchestration."""

    max_steps: int = 8
    allowed_kinds: set[str] = field(
        default_factory=lambda: {"memory", "repository", "git", "validation", "analysis"}
    )


class WorkflowValidator:
    """Validate workflow completeness, dependencies, and analysis-only safety."""

    unsafe_terms = (
        "modify",
        "edit",
        "patch",
        "delete",
        "commit changes",
        "create commit",
        "push",
        "deploy",
        "apply",
        "write file",
        "create file",
        "autonomous coding",
    )

    def __init__(self, limits: WorkflowLimits | None = None) -> None:
        self.limits = limits or WorkflowLimits()

    def validate_workflow(self, workflow: Workflow) -> WorkflowValidationResult:
        """Return validation errors and warnings for a workflow."""
        errors: list[str] = []
        warnings: list[str] = []
        if not workflow.steps:
            errors.append("Workflow has no steps.")
        if len(workflow.steps) > self.limits.max_steps:
            errors.append(f"Workflow has {len(workflow.steps)} steps; limit is {self.limits.max_steps}.")

        step_ids = {step.id for step in workflow.steps}
        seen: set[str] = set()
        for step in workflow.steps:
            if step.id in seen:
                errors.append(f"Duplicate workflow step id: {step.id}")
            seen.add(step.id)
            if step.kind not in self.limits.allowed_kinds:
                errors.append(f"Unauthorized workflow step kind: {step.kind}")
            for dependency in step.dependencies:
                if dependency not in step_ids:
                    errors.append(f"Step {step.id} depends on unknown step {dependency}.")
            text = f"{step.title} {step.task} {step.expected_outcome}".lower()
            for term in self.unsafe_terms:
                if term in text:
                    errors.append(f"Step {step.id} contains unsafe autonomous action wording: {term}")
        if not any(step.kind in {"repository", "memory"} for step in workflow.steps):
            warnings.append("Workflow does not include repository or memory context collection.")
        return WorkflowValidationResult(valid=not errors, warnings=warnings, errors=errors)


_default_validator: WorkflowValidator | None = None


def default_workflow_validator() -> WorkflowValidator:
    """Return a lazily created workflow validator."""
    global _default_validator
    if _default_validator is None:
        _default_validator = WorkflowValidator()
    return _default_validator


def validate_workflow(workflow: Workflow) -> WorkflowValidationResult:
    """Validate a workflow using the default validator."""
    return default_workflow_validator().validate_workflow(workflow)
