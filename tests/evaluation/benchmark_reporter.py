"""Human-readable reporting and failure analysis."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tests.evaluation.benchmark_models import BenchmarkFailure, BenchmarkReport


class BenchmarkReporter:
    """Format benchmark reports and derive actionable failure summaries."""

    def generate_report(self, report: BenchmarkReport) -> str:
        """Return a concise text report suitable for terminals or Slack."""
        lines = [
            "*Benchmark Report*",
            f"Run ID: `{report.run_id}`",
            f"Cases: {report.passed_cases} passed, {report.failed_cases} failed, {report.total_cases} total",
            f"Pass Rate: {report.pass_rate:.1%}",
            f"Duration: {report.duration_seconds:.3f}s",
        ]

        suite_metrics = [
            metric
            for metric in report.metrics
            if metric.suite_name and metric.name != "suite_accuracy"
        ]
        if suite_metrics:
            lines.extend(["", "*Accuracy Metrics:*"])
            for metric in suite_metrics:
                lines.append(
                    f"- {self._label(metric.name)} ({metric.suite_name}): {self._format_value(metric.value, metric.unit)}"
                )

        if report.failures:
            lines.extend(["", f"*Failing Cases:* {len(report.failures)}"])
            for failure in report.failures[:10]:
                lines.append(
                    f"- [{failure.suite_name}] {failure.case_name}: {failure.message}"
                )

            analysis = self.analyze_failures(report)
            if analysis["actionable_summaries"]:
                lines.extend(["", "*Failure Analysis:*"])
                lines.extend(
                    f"- {summary}"
                    for summary in analysis["actionable_summaries"][:6]
                )
        return "\n".join(lines)

    def analyze_failures(self, report: BenchmarkReport) -> dict[str, Any]:
        """Identify common failure categories and subsystem-specific regressions."""
        categories = Counter(failure.category for failure in report.failures)
        suites = Counter(failure.suite_name for failure in report.failures)
        routes = self._expected_values(report.failures, "route")
        workflows = self._expected_values(report.failures, "workflow_type")
        retrieval_queries = Counter(
            str(failure.metadata.get("query") or failure.case_name)
            for failure in report.failures
            if failure.suite_name == "retrieval"
        )

        summaries: list[str] = []
        if categories:
            category, count = categories.most_common(1)[0]
            summaries.append(f"Most common failure: {category} ({count} cases).")
        if suites:
            suite, count = suites.most_common(1)[0]
            summaries.append(f"Most affected suite: {suite} ({count} failures).")
        if routes:
            route, count = routes.most_common(1)[0]
            summaries.append(f"Most failing route: {route} ({count} cases).")
        if workflows:
            workflow, count = workflows.most_common(1)[0]
            summaries.append(f"Most failing workflow: {workflow} ({count} cases).")
        if retrieval_queries:
            query, count = retrieval_queries.most_common(1)[0]
            summaries.append(f"Most failing retrieval query: {query} ({count} cases).")

        return {
            "common_failures": dict(categories),
            "failing_suites": dict(suites),
            "failing_routes": dict(routes),
            "failing_workflows": dict(workflows),
            "failing_retrieval_queries": dict(retrieval_queries),
            "actionable_summaries": summaries,
        }

    def _expected_values(
        self,
        failures: tuple[BenchmarkFailure, ...],
        field_name: str,
    ) -> Counter[str]:
        values: Counter[str] = Counter()
        for failure in failures:
            expected = failure.expected
            if isinstance(expected, dict) and expected.get(field_name):
                values[str(expected[field_name])] += 1
        return values

    def _label(self, value: str) -> str:
        return value.replace("_", " ").title()

    def _format_value(self, value: float, unit: str) -> str:
        if unit == "count":
            return str(int(value))
        return f"{value:.1%}"
