"""Testable examples for controlled autonomous workflows."""

from __future__ import annotations

import unittest

from src.execution.execution_models import ExecutionSummary
from src.executor.task_executor import TaskExecutor
from src.planner.task_planner import TaskPlan, TaskPlanner
from src.workflows import (
    WorkflowBuilder,
    WorkflowContext,
    WorkflowEngine,
    WorkflowExecutor,
    WorkflowRegistry,
    WorkflowSelector,
    WorkflowStep,
    WorkflowValidator,
    list_workflows,
)
from src.workflows.workflow_models import Workflow


class FakeGitTool:
    """Minimal git tool stub for TaskExecutor workflow routing tests."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path


class FakeWorkflowEngine:
    """WorkflowEngine-compatible fake for executor routing tests."""

    def __init__(self) -> None:
        self.called = False

    def run_workflow(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> "FakeWorkflowSummary":
        self.called = True
        return FakeWorkflowSummary()


class FakeWorkflowSummary:
    """Minimal workflow summary fake."""

    def format_markdown(self) -> str:
        return "Workflow Engine\nBug Investigation Workflow"


class FakeExecutionEngine:
    """ExecutionEngine-compatible fake that records delegated workflow tasks."""

    def __init__(self) -> None:
        self.tasks: list[str] = []

    def execute_task(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> ExecutionSummary:
        self.tasks.append(task)
        return ExecutionSummary(
            execution_id=f"exec-{len(self.tasks)}",
            plan_id=f"plan-{len(self.tasks)}",
            goal=task,
            status="success",
            files_examined=["src/slack/slack_handler.py"],
            tools_executed=["repository.file_search"],
            issues_found=[],
            recommendations=["Continue with the listed repository areas."],
            findings_report="ok",
        )


class WorkflowSelectionTest(unittest.TestCase):
    """Examples for deterministic workflow selection and registry behavior."""

    def test_selects_expected_predefined_workflows(self) -> None:
        selector = WorkflowSelector()

        self.assertEqual(
            selector.select_workflow("Investigate duplicate Slack events").workflow_type,
            "bug_investigation",
        )
        self.assertEqual(
            selector.select_workflow("Explain authentication flow").workflow_type,
            "authentication_analysis",
        )
        self.assertEqual(
            selector.select_workflow("Why are tests failing?").workflow_type,
            "test_failure_investigation",
        )
        self.assertEqual(
            selector.select_workflow("Analyze recent repository changes").workflow_type,
            "git_analysis",
        )

    def test_registry_lists_predefined_workflows(self) -> None:
        names = [workflow.name for workflow in list_workflows()]

        self.assertIn("Bug Investigation Workflow", names)
        self.assertIn("Architecture Analysis Workflow", names)


class WorkflowBuilderValidatorTest(unittest.TestCase):
    """Examples for workflow building and safety validation."""

    def test_builds_dependency_ordered_workflow(self) -> None:
        workflow = WorkflowBuilder().build_workflow("Analyze authentication flow")

        self.assertEqual(workflow.workflow_type, "authentication_analysis")
        self.assertEqual(workflow.steps[0].dependencies, [])
        self.assertEqual(workflow.steps[1].dependencies, ["step-1"])
        self.assertIn("authentication", workflow.steps[1].task.lower())

    def test_validator_rejects_unsafe_workflow_wording(self) -> None:
        workflow = Workflow(
            id="unsafe",
            name="Unsafe Workflow",
            workflow_type="bug_investigation",
            description="Unsafe",
            steps=[
                WorkflowStep(
                    id="step-1",
                    title="Patch Code",
                    task="Modify repository files autonomously.",
                    kind="analysis",
                )
            ],
        )

        result = WorkflowValidator().validate_workflow(workflow)

        self.assertFalse(result.valid)
        self.assertTrue(any("unsafe" in error for error in result.errors))


class WorkflowExecutorTest(unittest.TestCase):
    """Examples for execution-engine delegation and report generation."""

    def test_executor_delegates_steps_to_execution_engine(self) -> None:
        execution_engine = FakeExecutionEngine()
        workflow = WorkflowBuilder().build_workflow("Investigate duplicate Slack events")
        context = WorkflowContext(
            workflow_id=workflow.id,
            task="Investigate duplicate Slack events",
            project_path="/repo",
        )

        summary = WorkflowExecutor(execution_engine=execution_engine).execute_workflow(
            workflow,
            context,
            request_id="workflow-test",
        )

        self.assertGreaterEqual(len(execution_engine.tasks), 1)
        self.assertIn("Workflow Engine", summary.format_markdown())
        self.assertIn("Bug Investigation Workflow", summary.format_markdown())
        self.assertIn("repository.file_search", summary.result.tools_used)

    def test_engine_runs_selected_workflow(self) -> None:
        execution_engine = FakeExecutionEngine()
        engine = WorkflowEngine(
            executor=WorkflowExecutor(execution_engine=execution_engine),
        )

        summary = engine.run_workflow(
            "Analyze recent repository changes",
            project_path="/repo",
            request_id="workflow-engine-test",
        )

        self.assertEqual(summary.result.workflow_type, "git_analysis")
        self.assertEqual(summary.result.status, "success")
        self.assertIn("Git Analysis Workflow", summary.format_markdown())


class WorkflowRoutingTest(unittest.TestCase):
    """Examples for Slack routing to controlled workflows."""

    def test_project_execution_plan_carries_workflow_flag(self) -> None:
        plan = TaskPlanner().create_plan("Investigate duplicate Slack events")

        self.assertEqual(plan.intent, "project_execution")
        self.assertTrue(plan.use_execution_engine)
        self.assertTrue(plan.use_workflow_engine)
        self.assertFalse(plan.use_repository_modifier)

    def test_task_executor_uses_workflow_engine_for_project_execution(self) -> None:
        workflow_engine = FakeWorkflowEngine()
        executor = TaskExecutor(
            git_tool=FakeGitTool(repo_path="/repo"),
            workflow_engine=workflow_engine,
        )

        result = executor.execute(
            TaskPlan(
                original_task="Investigate duplicate Slack events",
                clean_task="Investigate duplicate Slack events",
                intent="project_execution",
                use_execution_engine=True,
                use_workflow_engine=True,
            )
        )

        self.assertTrue(workflow_engine.called)
        self.assertIn("Workflow Engine", result)


if __name__ == "__main__":
    unittest.main()
