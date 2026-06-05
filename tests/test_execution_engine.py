"""Testable examples for safe read-only execution workflows."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from src.execution import (
    ExecutionEngine,
    ExecutionPlan,
    ExecutionValidator,
    ToolExecutionRequest,
)
from src.execution.execution_models import ExecutionStep
from src.executor.task_executor import TaskExecutor
from src.planner.task_planner import TaskPlan, TaskPlanner
from src.planning.execution_models import (
    Plan,
    PlanStep,
    PlanningContext,
    PlanningFileContext,
    TaskAnalysis,
)
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult
from src.tools.tool_registry import ToolRegistry, create_default_registry


class UnsafeWriteTool(BaseTool):
    """Non-read-only fake used to prove validation rejects unsafe tools."""

    metadata = ToolMetadata(
        name="unsafe.write",
        description="Pretend to write files.",
        category="unsafe",
        read_only=False,
    )

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        return self._success({"ignored": True})


class FakeGitTool:
    """Minimal git tool stub for TaskExecutor routing tests."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path


class FakePlanningEngine:
    """PlanningEngine-compatible fake for execution routing tests."""

    def __init__(self) -> None:
        self.called = False

    def create_plan(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> str:
        self.called = True
        return "planning-plan"


class FakeExecutionEngine:
    """ExecutionEngine-compatible fake for TaskExecutor tests."""

    def __init__(self) -> None:
        self.called = False

    def execute_plan(
        self,
        plan: object,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> "FakeSummary":
        self.called = True
        return FakeSummary()


class FakeSummary:
    """Minimal execution summary fake."""

    def format_markdown(self) -> str:
        return "Execution Engine\nRead-only investigation completed."


class ExecutionValidatorTest(unittest.TestCase):
    """Examples for execution safety validation."""

    def test_rejects_non_read_only_tools(self) -> None:
        registry = ToolRegistry([UnsafeWriteTool()])
        plan = ExecutionPlan(
            id="exec-1",
            goal="Unsafe write",
            project_path=".",
            steps=[
                ExecutionStep(
                    id="step-1",
                    title="Unsafe",
                    description="Attempt unsafe tool.",
                    tool_requests=[ToolExecutionRequest("unsafe.write")],
                )
            ],
        )

        result = ExecutionValidator(registry=registry).validate_execution_plan(plan)

        self.assertFalse(result.valid)
        self.assertTrue(any("not allowed" in error for error in result.errors))

    def test_rejects_explicit_test_command_override(self) -> None:
        plan = ExecutionPlan(
            id="exec-1",
            goal="Run tests",
            project_path=".",
            steps=[
                ExecutionStep(
                    id="step-1",
                    title="Run Tests",
                    description="Run tests safely.",
                    tool_requests=[
                        ToolExecutionRequest(
                            "validation.pytest",
                            {"command": "python -c 'print(1)'"},
                        )
                    ],
                )
            ],
        )

        result = ExecutionValidator(
            registry=create_default_registry()
        ).validate_execution_plan(plan)

        self.assertFalse(result.valid)
        self.assertTrue(any("Explicit test commands" in error for error in result.errors))


class ExecutionEngineTest(unittest.TestCase):
    """Examples for executing planning output through read-only tools."""

    def test_executes_plan_and_generates_findings_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            slack_dir = root / "src" / "slack"
            slack_dir.mkdir(parents=True)
            (slack_dir / "slack_handler.py").write_text(
                "def handle_slack_event(event):\n"
                "    return event.get('event_id')\n",
                encoding="utf-8",
            )
            plan = self._plan(root)

            summary = ExecutionEngine().execute_plan(
                plan,
                project_path=str(root),
                request_id="exec-test",
            )

            self.assertIn("system.file_reader", summary.tools_executed)
            self.assertIn("src/slack/slack_handler.py", summary.files_examined)
            self.assertIn("Execution Engine", summary.findings_report)
            self.assertEqual(summary.status, "success")

    def _plan(self, root: Path) -> Plan:
        analysis = TaskAnalysis(
            task_type="Investigation",
            complexity="Small",
            requires_repository_context=True,
            requires_git_context=False,
        )
        context = PlanningContext(
            task="Investigate duplicate Slack events",
            project_path=str(root),
            repository_files=[
                PlanningFileContext(
                    path="src/slack/slack_handler.py",
                    score=95,
                    reasons=["keyword:slack"],
                )
            ],
        )
        return Plan(
            goal="Investigate duplicate Slack events",
            analysis=analysis,
            context=context,
            steps=[
                PlanStep(
                    id="step-1",
                    title="Collect Repository Evidence",
                    description="Review relevant files and dependencies.",
                    dependencies=[],
                    risk_level="Low",
                    expected_outcome="Evidence is collected.",
                ),
                PlanStep(
                    id="step-2",
                    title="Summarize Findings",
                    description="Prepare findings from collected evidence.",
                    dependencies=["step-1"],
                    risk_level="Low",
                    expected_outcome="Findings are clear.",
                ),
            ],
        )


class ExecutionRoutingTest(unittest.TestCase):
    """Examples for Slack read-only execution routing."""

    def test_investigation_request_routes_to_execution_engine(self) -> None:
        plan = TaskPlanner().create_plan("Investigate duplicate Slack events")

        self.assertEqual(plan.intent, "project_execution")
        self.assertTrue(plan.use_execution_engine)
        self.assertFalse(plan.use_planning_engine)
        self.assertFalse(plan.use_repository_modifier)

    def test_review_recent_changes_uses_execution_engine_before_one_tool_route(self) -> None:
        plan = TaskPlanner().create_plan("Review recent repository changes")

        self.assertEqual(plan.intent, "project_execution")
        self.assertTrue(plan.use_execution_engine)
        self.assertIsNone(plan.selected_tool_name)

    def test_task_executor_returns_execution_findings(self) -> None:
        planning_engine = FakePlanningEngine()
        execution_engine = FakeExecutionEngine()
        executor = TaskExecutor(
            git_tool=FakeGitTool(repo_path="/repo"),
            planning_engine=planning_engine,
            execution_engine=execution_engine,
        )

        result = executor.execute(
            TaskPlan(
                original_task="Investigate duplicate Slack events",
                clean_task="Investigate duplicate Slack events",
                intent="project_execution",
                use_execution_engine=True,
            )
        )

        self.assertTrue(planning_engine.called)
        self.assertTrue(execution_engine.called)
        self.assertIn("Read-only investigation completed", result)


if __name__ == "__main__":
    unittest.main()
