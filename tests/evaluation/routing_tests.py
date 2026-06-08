"""Routing accuracy benchmark suite."""

from __future__ import annotations

from src.planner.task_planner import TaskPlanner
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class RoutingEvaluator:
    """Evaluate public planner routing without executing selected tools."""

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Return the planner route for one natural-language query."""
        planner = TaskPlanner()
        query = str(case.input_data["query"])
        plan = planner.create_plan(
            query,
            thread_ts=f"benchmark-{context.run_id}-{case.id}",
            channel="benchmark-routing",
            slack_user="benchmark-user",
            request_id=context.run_id,
        )
        actual = {
            "route": self._route_name(plan.intent, plan.selected_tool_name),
            "intent": plan.intent,
            "tool_name": plan.selected_tool_name,
            "normalized_query": plan.normalized_task,
        }
        expected = dict(case.expected_output)
        passed = all(actual.get(key) == value for key, value in expected.items())
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics={
                "routing_accuracy": float(passed),
                "tool_selection_accuracy": float(
                    not expected.get("tool_name")
                    or actual["tool_name"] == expected["tool_name"]
                ),
            },
            failure_message=(
                ""
                if passed
                else f"Expected route {expected!r}, received {actual!r}."
            ),
            failure_category="route_mismatch",
            metadata={"query": query},
        )

    def _route_name(self, intent: str, tool_name: str | None) -> str:
        if tool_name and tool_name.startswith("git."):
            return "git_tool"
        if intent == "greeting":
            return "greeting"
        if intent in {"general", "generic_code"}:
            return "general_knowledge"
        return intent


def create_routing_suite() -> BenchmarkSuite:
    """Return typo-aware routing benchmark cases."""
    evaluator = RoutingEvaluator()
    return BenchmarkSuite(
        name="routing",
        description="Measures deterministic intent and tool routing accuracy.",
        evaluator=evaluator.evaluate,
        metric_names=("routing_accuracy", "tool_selection_accuracy"),
        cases=[
            BenchmarkCase(
                id="routing-show-branches",
                name="Show branches routes to git",
                input_data={"query": "show branches"},
                expected_output={"route": "git_tool", "tool_name": "git.branch"},
                category="git_route",
            ),
            BenchmarkCase(
                id="routing-typo-branches",
                name="Typo branch request routes to git",
                input_data={"query": "show braches"},
                expected_output={"route": "git_tool", "tool_name": "git.branch"},
                category="git_route",
                tags=("typo",),
            ),
            BenchmarkCase(
                id="routing-list-commits",
                name="Commit list routes to git log",
                input_data={"query": "list all commits"},
                expected_output={"route": "git_tool", "tool_name": "git.log"},
                category="git_route",
            ),
            BenchmarkCase(
                id="routing-greeting",
                name="Greeting stays conversational",
                input_data={"query": "hello"},
                expected_output={"route": "greeting"},
                category="conversation_route",
            ),
            BenchmarkCase(
                id="routing-general-knowledge",
                name="General knowledge avoids repository execution",
                input_data={"query": "what is JWT"},
                expected_output={"route": "general_knowledge"},
                category="general_route",
            ),
        ],
    )
