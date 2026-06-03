"""Testable examples for the thinking-only planning engine."""

from __future__ import annotations

import tempfile
import unittest

from src.executor.task_executor import TaskExecutor
from src.planner.task_planner import TaskPlan, TaskPlanner
from src.planning.execution_models import (
    Plan,
    PlanStep,
    PlanValidationResult,
    PlanningContext,
    PlanningFileContext,
    PlanningSymbolContext,
    TaskAnalysis,
)
from src.planning.plan_generator import PlanGenerator
from src.planning.plan_validator import PlanValidator
from src.planning.planner import PlanningEngine
from src.planning.task_analyzer import TaskAnalyzer
from src.retrieval.retrieval_models import RankedFile, RankedSymbol, RetrievalContext, RetrievalResult
from src.router.intent_router import IntentRouter


class FakeRetrievalEngine:
    """RepositoryRetrievalEngine-compatible fake for deterministic planning tests."""

    def retrieve_context(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
    ) -> RetrievalResult:
        """Return fixed repository context for planning."""
        files = [
            RankedFile(
                path="src/slack/slack_handler.py",
                score=92,
                reasons=["keyword:slack", "dependency:event"],
                dependencies=["src/tools/git_tool.py"],
                dependents=["app.py"],
            ),
            RankedFile(
                path="app.py",
                score=74,
                reasons=["dependent-of:src/slack/slack_handler.py"],
            ),
        ]
        symbols = [
            RankedSymbol(
                name="handle_slack_event",
                kind="function",
                file_path="src/slack/slack_handler.py",
                score=88,
                line_start=10,
                line_end=45,
                reasons=["symbol-match"],
            )
        ]
        context = RetrievalContext(
            query=query,
            files=files,
            symbols=symbols,
            snippets=[],
            repository_summary={
                "metadata": {"branch": "main", "head_commit": "abc123"},
                "statistics": {"file_count": 8},
                "git": {"changed_files": ["src/slack/slack_handler.py"]},
            },
            ranking_decisions=["keyword and dependency signals selected Slack handler"],
        )
        return RetrievalResult(query=query, terms=["slack"], files=files, symbols=symbols, context=context)


class FakeGitTool:
    """GitTool-compatible fake that only exposes read-only context."""

    def __init__(self, repo_path: str) -> None:
        self._repo_path = repo_path
        self.commands: list[list[str]] = []

    @property
    def repo_path(self) -> str:
        """Return fake repository path."""
        return self._repo_path

    def is_git_repo(self) -> bool:
        """Return True for planning tests."""
        return True

    def run_command(self, args: list[str]) -> str:
        """Return deterministic read-only git output."""
        self.commands.append(args)
        mapping = {
            ("branch", "--show-current"): "main",
            ("rev-parse", "HEAD"): "abc123def456",
            ("diff", "--name-only"): "src/slack/slack_handler.py",
            ("diff", "--cached", "--name-only"): "",
            ("ls-files", "--others", "--exclude-standard"): "",
            ("log", "--oneline", "-5"): "abc123 Fix Slack retry handling",
            ("log", "--name-only", "--pretty=format:", "--max-count=5"): "src/slack/slack_handler.py\napp.py",
        }
        return mapping.get(tuple(args), "")


class FakePlanningEngine:
    """PlanningEngine-compatible fake for executor routing tests."""

    def __init__(self) -> None:
        self.called = False

    def create_plan(
        self,
        task: str,
        project_path: str | None = None,
        request_id: str | None = None,
    ) -> "FakeFormattedPlan":
        """Return a fake formatted plan."""
        self.called = True
        return FakeFormattedPlan()


class FakeFormattedPlan:
    """Minimal formatted plan object."""

    def format_markdown(self) -> str:
        """Return fake Slack text."""
        return "Planning Engine\nNo code, git, or filesystem actions were executed."


class TaskAnalyzerTest(unittest.TestCase):
    """Examples for task classification and complexity estimation."""

    def test_classifies_bug_fix_and_estimates_medium_complexity(self) -> None:
        analysis = TaskAnalyzer().analyze_task("Fix duplicate Slack event processing")

        self.assertEqual(analysis.task_type, "Bug Fix")
        self.assertEqual(analysis.complexity, "Medium")
        self.assertTrue(analysis.requires_repository_context)
        self.assertTrue(analysis.requires_git_context)

    def test_classifies_configuration_change(self) -> None:
        analysis = TaskAnalyzer().analyze_task("Create a plan for changing Gemini model configuration")

        self.assertEqual(analysis.task_type, "Configuration Change")
        self.assertTrue(analysis.requires_repository_context)


class PlanGeneratorTest(unittest.TestCase):
    """Examples for deterministic plan generation."""

    def test_generates_dependency_ordered_repository_aware_plan(self) -> None:
        context = PlanningContext(
            task="Add JWT refresh token support",
            project_path="/repo",
            repository_files=[
                PlanningFileContext(path="src/auth.py", score=90, reasons=["keyword:jwt"]),
                PlanningFileContext(path="src/jwt_service.py", score=85, reasons=["dependency-of:src/auth.py"]),
            ],
            repository_symbols=[
                PlanningSymbolContext(
                    name="JWTService",
                    kind="class",
                    file_path="src/jwt_service.py",
                    line_start=1,
                    line_end=20,
                )
            ],
        )
        analysis = TaskAnalyzer().analyze_task(context.task, context=context)

        plan = PlanGenerator().generate_plan(context.task, analysis, context=context)

        self.assertEqual(plan.goal, "Add JWT refresh token support")
        self.assertEqual(plan.steps[0].dependencies, [])
        self.assertEqual(plan.steps[1].dependencies, ["step-1"])
        self.assertIn("src/auth.py", plan.steps[0].description)
        self.assertFalse(plan.metadata["executes_plan"])


class PlanValidatorTest(unittest.TestCase):
    """Examples for plan validation."""

    def test_detects_duplicate_steps_unknown_dependencies_and_execution_wording(self) -> None:
        analysis = TaskAnalysis(
            task_type="Bug Fix",
            complexity="Small",
            requires_repository_context=True,
            requires_git_context=True,
        )
        plan = Plan(
            goal="Fix retry bug",
            analysis=analysis,
            context=PlanningContext(task="Fix retry bug"),
            steps=[
                PlanStep(
                    id="step-1",
                    title="Locate Flow",
                    description="Find the current retry flow.",
                    dependencies=[],
                    risk_level="Low",
                    expected_outcome="Flow is known.",
                ),
                PlanStep(
                    id="step-1",
                    title="Locate Flow",
                    description="Run the command to apply patch.",
                    dependencies=["step-9"],
                    risk_level="High",
                    expected_outcome="Command is run.",
                ),
            ],
            validation=PlanValidationResult(valid=True),
        )

        result = PlanValidator().validate_plan(plan)

        self.assertFalse(result.valid)
        self.assertTrue(any("Duplicate step id" in error for error in result.errors))
        self.assertTrue(any("unknown step" in error for error in result.errors))
        self.assertTrue(any("execution-like wording" in warning for warning in result.warnings))


class PlanningEngineTest(unittest.TestCase):
    """Examples for the full planning orchestrator."""

    def test_create_plan_attaches_repository_and_git_context_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = PlanningEngine(
                retrieval_engine=FakeRetrievalEngine(),
                git_tool=FakeGitTool(repo_path=tmp),
            )

            plan = engine.create_plan("Create a plan for fixing duplicate Slack event processing")
            formatted = plan.format_markdown()

            self.assertEqual(plan.analysis.task_type, "Bug Fix")
            self.assertTrue(plan.validation.valid)
            self.assertIn("src/slack/slack_handler.py", [file.path for file in plan.context.repository_files])
            self.assertEqual(plan.context.git.branch, "main")
            self.assertIn("No code, git, or filesystem actions were executed.", formatted)


class PlanningRoutingTest(unittest.TestCase):
    """Examples for Slack planning intent routing."""

    def test_plan_request_routes_to_planning_engine_not_modifier_or_git(self) -> None:
        plan = TaskPlanner().create_plan("create a plan for adding tests for src/slack/slack_handler.py")

        self.assertEqual(plan.intent, "planning")
        self.assertTrue(plan.use_planning_engine)
        self.assertFalse(plan.run_git_action)
        self.assertFalse(plan.use_repository_modifier)

    def test_how_would_you_fix_routes_to_planning(self) -> None:
        self.assertEqual(IntentRouter().classify("How would you fix this bug?"), "planning")

    def test_executor_returns_formatted_plan(self) -> None:
        planning_engine = FakePlanningEngine()
        executor = TaskExecutor(
            git_tool=FakeGitTool(repo_path="/repo"),
            planning_engine=planning_engine,
        )
        result = executor.execute(
            TaskPlan(
                original_task="Create a plan for fixing duplicate events",
                clean_task="Create a plan for fixing duplicate events",
                intent="planning",
                use_planning_engine=True,
            )
        )

        self.assertTrue(planning_engine.called)
        self.assertIn("Planning Engine", result)


if __name__ == "__main__":
    unittest.main()
