"""Build executable workflows from selected templates."""

from __future__ import annotations

from src.workflows.workflow_models import Workflow, WorkflowSelection, WorkflowStep, stable_workflow_id
from src.workflows.workflow_registry import WorkflowRegistry, default_workflow_registry
from src.workflows.workflow_selector import WorkflowSelector, default_workflow_selector


class WorkflowBuilder:
    """Convert a user task and selected template into ordered workflow steps."""

    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        selector: WorkflowSelector | None = None,
    ) -> None:
        self.registry = registry or default_workflow_registry()
        self.selector = selector or default_workflow_selector()

    def build_workflow(
        self,
        task: str,
        selection: WorkflowSelection | None = None,
    ) -> Workflow:
        """Build a workflow for a task."""
        selection = selection or self.selector.select_workflow(task)
        template = self.registry.get_workflow(selection.workflow_type)
        if template is None:
            raise ValueError(f"Workflow template not registered: {selection.workflow_type}")

        steps: list[WorkflowStep] = []
        for index, step_template in enumerate(template.steps, start=1):
            step_id = f"step-{index}"
            steps.append(
                WorkflowStep(
                    id=step_id,
                    title=step_template.title,
                    task=step_template.task_template.format(task=task),
                    kind=step_template.kind,
                    dependencies=[] if index == 1 else [f"step-{index - 1}"],
                    expected_outcome=step_template.expected_outcome,
                    metadata={"template_index": index},
                )
            )
        return Workflow(
            id=stable_workflow_id(task, selection.workflow_type),
            name=template.name,
            workflow_type=template.workflow_type,
            description=template.description,
            steps=steps,
            tags=list(template.tags),
            metadata={
                "selection_confidence": selection.confidence,
                "selection_reasons": list(selection.reasons),
                "task": task,
            },
        )


_default_builder: WorkflowBuilder | None = None


def default_workflow_builder() -> WorkflowBuilder:
    """Return a lazily created workflow builder."""
    global _default_builder
    if _default_builder is None:
        _default_builder = WorkflowBuilder()
    return _default_builder


def build_workflow(task: str, selection: WorkflowSelection | None = None) -> Workflow:
    """Build a workflow using the default builder."""
    return default_workflow_builder().build_workflow(task, selection=selection)
