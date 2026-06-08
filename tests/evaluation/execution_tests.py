"""Read-only execution correctness benchmark suite."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

from src.execution import (
    ExecutionContext,
    ExecutionCoordinator,
    ExecutionPlan,
    ExecutionStep,
    ExecutionValidator,
    StepExecutor,
    ToolExecutionRequest,
)
from src.tools.repository.file_search_tool import FileSearchTool
from src.tools.repository.symbol_search_tool import SymbolSearchTool
from src.tools.system.directory_tree_tool import DirectoryTreeTool
from src.tools.tool_executor import ToolExecutor
from src.tools.tool_registry import ToolRegistry
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class ExecutionEvaluator:
    """Execute bounded read-only tool requests without memory persistence."""

    def __init__(self, coordinator: ExecutionCoordinator | None = None) -> None:
        self.coordinator = coordinator
        self._default_coordinator: ExecutionCoordinator | None = None

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Execute one safe plan and verify tool and status outcomes."""
        request = ToolExecutionRequest(
            tool_name=str(case.input_data["tool_name"]),
            tool_input=dict(case.input_data.get("tool_input") or {}),
            reason=str(case.input_data.get("reason") or "Benchmark read-only execution."),
            required=True,
        )
        plan = ExecutionPlan(
            id=f"benchmark-plan-{case.id}",
            goal=str(case.input_data["task"]),
            project_path=context.project_path,
            steps=[
                ExecutionStep(
                    id="step-1",
                    title=case.name,
                    description=str(case.input_data["task"]),
                    tool_requests=[request],
                    expected_outcome="The read-only tool completes successfully.",
                )
            ],
            metadata={"benchmark": True, "read_only": True},
        )
        execution_context = ExecutionContext(
            execution_id=f"{context.run_id}-{case.id}",
            project_path=context.project_path,
        )
        with self._benchmark_cache(context.project_path):
            coordinator = self.coordinator or self._coordinator()
            summary = coordinator.execute_plan(
                plan,
                execution_context,
                request_id=context.run_id,
            )
        expected_tool = str(case.expected_output["tool_name"])
        status_ok = summary.status == str(case.expected_output.get("status") or "success")
        tool_ok = expected_tool in summary.tools_executed
        passed = status_ok and tool_ok
        actual = {
            "status": summary.status,
            "tools_executed": summary.tools_executed,
            "failures": summary.failures,
            "files_examined": summary.files_examined,
        }
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics={
                "execution_accuracy": float(passed),
                "execution_success_rate": float(status_ok),
                "tool_selection_accuracy": float(tool_ok),
            },
            failure_message=(
                ""
                if passed
                else f"Expected successful {expected_tool} execution, received {actual!r}."
            ),
            failure_category="execution_failure",
            metadata={"task": case.input_data["task"]},
        )

    def _coordinator(self) -> ExecutionCoordinator:
        """Create a coordinator with only the tools exercised by this suite."""
        if self._default_coordinator is not None:
            return self._default_coordinator
        registry = ToolRegistry(
            [
                FileSearchTool(),
                SymbolSearchTool(),
                DirectoryTreeTool(),
            ]
        )
        validator = ExecutionValidator(registry=registry)
        step_executor = StepExecutor(
            tool_executor=ToolExecutor(registry=registry),
            validator=validator,
        )
        self._default_coordinator = ExecutionCoordinator(
            step_executor=step_executor,
            validator=validator,
        )
        return self._default_coordinator

    @contextmanager
    def _benchmark_cache(self, project_path: str) -> Iterator[None]:
        """Keep repository-state cache writes outside the evaluated repository."""
        previous = os.environ.get("REPOSITORY_STATE_CACHE_DIR")
        digest = hashlib.sha1(project_path.encode("utf-8")).hexdigest()[:12]
        cache_dir = os.path.join(
            tempfile.gettempdir(),
            "slack-claude-bot-benchmarks",
            digest,
        )
        os.environ["REPOSITORY_STATE_CACHE_DIR"] = cache_dir
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("REPOSITORY_STATE_CACHE_DIR", None)
            else:
                os.environ["REPOSITORY_STATE_CACHE_DIR"] = previous


def create_execution_suite() -> BenchmarkSuite:
    """Return safe tool-execution benchmark cases."""
    evaluator = ExecutionEvaluator()
    return BenchmarkSuite(
        name="execution",
        description="Measures read-only execution and tool completion correctness.",
        evaluator=evaluator.evaluate,
        metric_names=(
            "execution_accuracy",
            "execution_success_rate",
            "tool_selection_accuracy",
        ),
        cases=[
            BenchmarkCase(
                id="execution-repository-investigation",
                name="Repository investigation executes file search",
                input_data={
                    "task": "Investigate Slack event processing",
                    "tool_name": "repository.file_search",
                    "tool_input": {
                        "query": "Slack event",
                        "search_content": True,
                        "max_results": 10,
                    },
                },
                expected_output={"tool_name": "repository.file_search", "status": "success"},
                category="tool_execution",
            ),
            BenchmarkCase(
                id="execution-architecture-analysis",
                name="Architecture analysis executes directory tree",
                input_data={
                    "task": "Analyze repository architecture",
                    "tool_name": "system.directory_tree",
                    "tool_input": {"path": ".", "max_depth": 2, "max_entries": 120},
                },
                expected_output={"tool_name": "system.directory_tree", "status": "success"},
                category="tool_execution",
            ),
            BenchmarkCase(
                id="execution-authentication-trace",
                name="Authentication trace executes symbol search",
                input_data={
                    "task": "Trace authentication handling",
                    "tool_name": "repository.symbol_search",
                    "tool_input": {"query": "auth", "kind": "any", "max_results": 10},
                },
                expected_output={"tool_name": "repository.symbol_search", "status": "success"},
                category="tool_execution",
            ),
        ],
    )
