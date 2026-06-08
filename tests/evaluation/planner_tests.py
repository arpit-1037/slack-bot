"""Planning quality benchmark suite."""

from __future__ import annotations

from src.planning.execution_models import PlanningContext
from src.planning.plan_generator import PlanGenerator
from src.planning.task_analyzer import TaskAnalyzer
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class PlannerEvaluator:
    """Measure deterministic plan coverage without executing the plan."""

    def __init__(self) -> None:
        self.analyzer = TaskAnalyzer()
        self.generator = PlanGenerator()

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Generate a plan and score expected capability coverage."""
        task = str(case.input_data["task"])
        planning_context = PlanningContext(task=task, project_path=context.project_path)
        analysis = self.analyzer.analyze_task(task, context=planning_context)
        plan = self.generator.generate_plan(task, analysis, context=planning_context)
        flattened = " ".join(
            " ".join([step.title, step.description, step.expected_outcome])
            for step in plan.steps
        ).lower()
        concepts = dict(case.expected_output["concepts"])
        matched = {
            concept: any(term.lower() in flattened for term in terms)
            for concept, terms in concepts.items()
        }
        score = sum(matched.values()) / len(matched) if matched else 0.0
        expected_type = case.expected_output.get("task_type")
        type_ok = expected_type is None or analysis.task_type == expected_type
        passed = score >= float(case.metadata.get("minimum_coverage", 0.75)) and type_ok
        actual = {
            "task_type": analysis.task_type,
            "complexity": analysis.complexity,
            "step_titles": [step.title for step in plan.steps],
            "concepts_matched": matched,
            "coverage": round(score, 6),
        }
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics={"planning_accuracy": score},
            failure_message=(
                ""
                if passed
                else f"Plan covered {score:.1%} of required concepts: {matched!r}."
            ),
            failure_category="planning_coverage",
            metadata={"task": task},
        )


def create_planner_suite() -> BenchmarkSuite:
    """Return deterministic plan quality cases."""
    evaluator = PlannerEvaluator()
    return BenchmarkSuite(
        name="planner",
        description="Measures task classification and expected plan capability coverage.",
        evaluator=evaluator.evaluate,
        metric_names=("planning_accuracy",),
        cases=[
            BenchmarkCase(
                id="planner-duplicate-events",
                name="Duplicate event plan covers investigation essentials",
                input_data={"task": "Investigate duplicate Slack events"},
                expected_output={
                    "task_type": "Bug Fix",
                    "concepts": {
                        "repository_search": ["entry point", "handlers", "repository"],
                        "git_analysis": ["recent repository activity", "recent changes"],
                        "validation": ["test", "verify", "validation"],
                        "evidence_collection": ["signals", "evidence", "reproduce"],
                    },
                },
                category="plan_quality",
            ),
            BenchmarkCase(
                id="planner-auth-trace",
                name="Authentication trace plan covers repository evidence",
                input_data={"task": "Trace the authentication flow in the repository"},
                expected_output={
                    "task_type": "Investigation",
                    "concepts": {
                        "repository_search": ["repository areas", "files", "symbols"],
                        "dependency_analysis": ["dependencies", "upstream", "downstream"],
                        "evidence_collection": ["evidence", "confirmed facts"],
                        "summary": ["summarize", "explanation"],
                    },
                },
                category="plan_quality",
            ),
        ],
    )
