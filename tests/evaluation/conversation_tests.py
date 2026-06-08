"""Conversation context and follow-up benchmark suite."""

from __future__ import annotations

from src.planner.task_planner import TaskPlanner
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class ConversationEvaluator:
    """Evaluate thread-aware follow-up resolution through TaskPlanner."""

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Run a conversation sequence and inspect the final thread state."""
        planner = TaskPlanner()
        thread_ts = f"benchmark-{context.run_id}-{case.id}"
        plans = [
            planner.create_plan(
                str(turn),
                thread_ts=thread_ts,
                channel="benchmark-conversation",
                slack_user="benchmark-user",
                request_id=context.run_id,
            )
            for turn in case.input_data["turns"]
        ]
        state = planner.conversation_tracker.get_state(
            thread_ts=thread_ts,
            channel="benchmark-conversation",
            slack_user="benchmark-user",
        )
        followups = [
            bool(plan.query_analysis and plan.query_analysis.followup and plan.query_analysis.followup.is_followup)
            for plan in plans[1:]
        ]
        actual = {
            "active_topic": state.active_topic,
            "active_tool_name": state.active_tool_name,
            "last_resolved_query": state.last_resolved_query,
            "followups_resolved": followups,
        }
        expected = dict(case.expected_output)
        topic_ok = (
            "active_topic" not in expected
            or actual["active_topic"] == expected["active_topic"]
        )
        tool_ok = (
            "active_tool_name" not in expected
            or actual["active_tool_name"] == expected["active_tool_name"]
        )
        expected_followups = expected.get("followups_resolved")
        followup_ok = (
            followups == expected_followups
            if expected_followups is not None
            else all(followups)
        )
        resolved_ok = (
            "last_resolved_query" not in expected
            or actual["last_resolved_query"] == expected["last_resolved_query"]
        )
        retained_terms = expected.get("retained_terms", [])
        retention_ok = all(
            term.lower() in actual["last_resolved_query"].lower()
            for term in retained_terms
        )
        passed = topic_ok and tool_ok and followup_ok and resolved_ok and retention_ok
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics={
                "conversation_accuracy": float(passed),
                "context_retention": float(tool_ok and retention_ok),
                "topic_tracking": float(topic_ok),
            },
            failure_message=(
                ""
                if passed
                else f"Conversation context drifted from {expected!r} to {actual!r}."
            ),
            failure_category="conversation_context_loss",
            metadata={"turns": list(case.input_data["turns"])},
        )


def create_conversation_suite() -> BenchmarkSuite:
    """Return multi-turn context retention cases."""
    evaluator = ConversationEvaluator()
    return BenchmarkSuite(
        name="conversation",
        description="Measures follow-up resolution, context retention, and topic tracking.",
        evaluator=evaluator.evaluate,
        metric_names=("conversation_accuracy", "context_retention", "topic_tracking"),
        cases=[
            BenchmarkCase(
                id="conversation-branch-followups",
                name="Branch context survives why and retry",
                input_data={"turns": ["show branches", "why?", "try again"]},
                expected_output={
                    "active_topic": "git",
                    "active_tool_name": "git.branch",
                    "retained_terms": ["branches"],
                },
                category="followup_context",
            ),
            BenchmarkCase(
                id="conversation-commit-expansion",
                name="Commit context survives expansion",
                input_data={"turns": ["show recent commits", "show more"]},
                expected_output={
                    "active_topic": "git",
                    "active_tool_name": "git.log",
                    "retained_terms": ["commits"],
                },
                category="followup_context",
            ),
            *[
                BenchmarkCase(
                    id=f"conversation-raw-git-{index}",
                    name=f"Raw git command remains unchanged: {query}",
                    input_data={"turns": ["show branches", query]},
                    expected_output={
                        "active_topic": "git",
                        "last_resolved_query": query,
                        "followups_resolved": [False],
                    },
                    category="raw_git_context_isolation",
                )
                for index, query in enumerate(
                    (
                        "git log",
                        "git status",
                        "git branch -a",
                        "git diff HEAD~1 HEAD",
                    ),
                    start=1,
                )
            ],
        ],
    )
