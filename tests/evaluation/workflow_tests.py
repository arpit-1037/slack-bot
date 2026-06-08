"""Workflow selection accuracy benchmark suite."""

from __future__ import annotations

from src.workflows.workflow_selector import WorkflowSelector
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class WorkflowEvaluator:
    """Evaluate deterministic workflow selection."""

    def __init__(self, selector: WorkflowSelector | None = None) -> None:
        self.selector = selector or WorkflowSelector()

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Return the selected workflow type and confidence."""
        task = str(case.input_data["task"])
        selection = self.selector.select_workflow(task)
        actual = {
            "workflow_type": selection.workflow_type,
            "workflow_name": selection.workflow_name,
            "confidence": selection.confidence,
        }
        expected = dict(case.expected_output)
        passed = all(actual.get(key) == value for key, value in expected.items())
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics={"workflow_accuracy": float(passed)},
            failure_message=(
                ""
                if passed
                else f"Expected workflow {expected!r}, received {actual!r}."
            ),
            failure_category="workflow_mismatch",
            metadata={"task": task},
        )


def create_workflow_suite() -> BenchmarkSuite:
    """Return workflow selection benchmark cases."""
    evaluator = WorkflowEvaluator()
    return BenchmarkSuite(
        name="workflow",
        description="Measures predefined workflow selection accuracy.",
        evaluator=evaluator.evaluate,
        metric_names=("workflow_accuracy",),
        cases=[
            BenchmarkCase(
                id="workflow-duplicate-events",
                name="Duplicate events use bug investigation",
                input_data={"task": "Investigate duplicate Slack events"},
                expected_output={"workflow_type": "bug_investigation"},
                category="workflow_selection",
            ),
            BenchmarkCase(
                id="workflow-authentication",
                name="Authentication explanation uses auth workflow",
                input_data={"task": "Explain authentication flow"},
                expected_output={"workflow_type": "authentication_analysis"},
                category="workflow_selection",
            ),
            BenchmarkCase(
                id="workflow-recent-changes",
                name="Recent changes use git analysis",
                input_data={"task": "Analyze recent repository changes"},
                expected_output={"workflow_type": "git_analysis"},
                category="workflow_selection",
            ),
            BenchmarkCase(
                id="workflow-failing-tests",
                name="Failing tests use test investigation",
                input_data={"task": "Why are pytest tests failing?"},
                expected_output={"workflow_type": "test_failure_investigation"},
                category="workflow_selection",
            ),
        ],
    )
