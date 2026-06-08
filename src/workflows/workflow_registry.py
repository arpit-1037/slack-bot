"""Registry for controlled autonomous workflow templates."""

from __future__ import annotations

from collections.abc import Iterable

from src.workflows.workflow_models import WorkflowType
from src.workflows.workflow_templates import WorkflowTemplate, predefined_templates


class WorkflowRegistry:
    """Register, discover, list, and fetch predefined workflows."""

    def __init__(self, workflows: Iterable[WorkflowTemplate] | None = None) -> None:
        self._workflows: dict[WorkflowType, WorkflowTemplate] = {}
        for workflow in workflows or predefined_templates().values():
            self.register_workflow(workflow)

    def register_workflow(self, workflow: WorkflowTemplate) -> WorkflowTemplate:
        """Register a workflow template."""
        self._workflows[workflow.workflow_type] = workflow
        return workflow

    def get_workflow(self, workflow_type: WorkflowType | str) -> WorkflowTemplate | None:
        """Return a workflow template by type."""
        return self._workflows.get(str(workflow_type))  # type: ignore[arg-type]

    def list_workflows(self) -> list[WorkflowTemplate]:
        """Return registered workflows sorted by name."""
        return sorted(self._workflows.values(), key=lambda item: item.name)

    def discover_workflows(self) -> list[str]:
        """Return registered workflow type names."""
        return sorted(self._workflows)


_default_registry: WorkflowRegistry | None = None


def default_workflow_registry() -> WorkflowRegistry:
    """Return a lazily created workflow registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = WorkflowRegistry()
    return _default_registry


def register_workflow(workflow: WorkflowTemplate) -> WorkflowTemplate:
    """Register a workflow with the default registry."""
    return default_workflow_registry().register_workflow(workflow)


def get_workflow(workflow_type: WorkflowType | str) -> WorkflowTemplate | None:
    """Return a workflow from the default registry."""
    return default_workflow_registry().get_workflow(workflow_type)


def list_workflows() -> list[WorkflowTemplate]:
    """List workflows from the default registry."""
    return default_workflow_registry().list_workflows()
