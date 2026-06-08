"""Git routing and repository-awareness benchmark suite."""

from __future__ import annotations

from src.planner.task_planner import TaskPlanner
from src.tools.git_tool import GitTool
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class GitEvaluator:
    """Evaluate git tool selection against the configured repository."""

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Return selected git tool and repository-awareness state."""
        query = str(case.input_data["query"])
        plan = TaskPlanner().create_plan(
            query,
            thread_ts=f"benchmark-{context.run_id}-{case.id}",
            channel="benchmark-git",
            slack_user="benchmark-user",
            request_id=context.run_id,
        )
        git_tool = GitTool(repo_path=context.project_path)
        actual = {
            "tool_name": plan.selected_tool_name,
            "intent": plan.intent,
            "is_git_repository": git_tool.is_git_repo(),
            "branch": git_tool.run_command(["branch", "--show-current"]),
        }
        expected = dict(case.expected_output)
        tool_ok = actual["tool_name"] == expected["tool_name"]
        repo_ok = actual["is_git_repository"] is bool(expected.get("is_git_repository", True))
        passed = tool_ok and repo_ok
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics={
                "tool_selection_accuracy": float(tool_ok),
                "repository_awareness": float(repo_ok),
            },
            failure_message=(
                ""
                if passed
                else f"Expected git behavior {expected!r}, received {actual!r}."
            ),
            failure_category="git_tool_mismatch",
            metadata={"query": query},
        )


def create_git_suite() -> BenchmarkSuite:
    """Return git intent and tool-selection cases."""
    evaluator = GitEvaluator()
    return BenchmarkSuite(
        name="git",
        description="Measures git tool selection and repository awareness.",
        evaluator=evaluator.evaluate,
        metric_names=("tool_selection_accuracy", "repository_awareness"),
        cases=[
            BenchmarkCase(
                id="git-branches",
                name="Branch query selects branch tool",
                input_data={"query": "show branches"},
                expected_output={"tool_name": "git.branch", "is_git_repository": True},
                category="git_tool_selection",
            ),
            BenchmarkCase(
                id="git-last-commit",
                name="Last commit selects log tool",
                input_data={"query": "last commit"},
                expected_output={"tool_name": "git.log", "is_git_repository": True},
                category="git_tool_selection",
            ),
            BenchmarkCase(
                id="git-what-changed",
                name="Change query selects diff tool",
                input_data={"query": "what changed"},
                expected_output={"tool_name": "git.diff", "is_git_repository": True},
                category="git_tool_selection",
            ),
            BenchmarkCase(
                id="git-compare-commits",
                name="Commit comparison selects diff tool",
                input_data={"query": "compare commits"},
                expected_output={"tool_name": "git.diff", "is_git_repository": True},
                category="git_tool_selection",
            ),
        ],
    )
