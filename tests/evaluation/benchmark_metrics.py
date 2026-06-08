"""Metric calculations for benchmark cases and reports."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from tests.evaluation.benchmark_models import BenchmarkMetric, BenchmarkResult


class BenchmarkMetrics:
    """Calculate deterministic accuracy, precision, recall, and aggregate scores."""

    def accuracy(
        self,
        passed: int,
        total: int,
        name: str = "accuracy",
        suite_name: str = "",
    ) -> BenchmarkMetric:
        """Return an accuracy metric from pass and total counts."""
        value = passed / total if total else 0.0
        return BenchmarkMetric(
            name=name,
            value=round(value, 6),
            suite_name=suite_name,
            description=f"{passed} of {total} cases passed",
            metadata={"passed": passed, "total": total},
        )

    def precision_recall(
        self,
        retrieved: Iterable[str],
        relevant: Iterable[str],
    ) -> dict[str, float]:
        """Return set-based retrieval precision and recall."""
        retrieved_set = {str(item) for item in retrieved}
        relevant_set = {str(item) for item in relevant}
        intersection = retrieved_set & relevant_set
        precision = len(intersection) / len(retrieved_set) if retrieved_set else 0.0
        recall = len(intersection) / len(relevant_set) if relevant_set else 0.0
        return {
            "retrieval_precision": round(precision, 6),
            "retrieval_recall": round(recall, 6),
        }

    def top_k_accuracy(
        self,
        ranked_items: Iterable[str],
        relevant: Iterable[str],
        k: int,
    ) -> float:
        """Return one when a relevant item appears in the first k results."""
        relevant_set = {str(item) for item in relevant}
        top_items = list(ranked_items)[: max(k, 0)]
        return 1.0 if any(item in relevant_set for item in top_items) else 0.0

    def aggregate_results(self, results: Iterable[BenchmarkResult]) -> list[BenchmarkMetric]:
        """Aggregate pass rates and case-level metrics by suite."""
        result_list = list(results)
        grouped: dict[str, list[BenchmarkResult]] = defaultdict(list)
        for result in result_list:
            grouped[result.suite_name].append(result)

        metrics: list[BenchmarkMetric] = [
            self.accuracy(
                sum(1 for result in result_list if result.passed),
                len(result_list),
                name="overall_accuracy",
            )
        ]
        for suite_name in sorted(grouped):
            suite_results = grouped[suite_name]
            metrics.append(
                self.accuracy(
                    sum(1 for result in suite_results if result.passed),
                    len(suite_results),
                    name="suite_accuracy",
                    suite_name=suite_name,
                )
            )
            values: dict[str, list[float]] = defaultdict(list)
            for result in suite_results:
                for metric in result.metrics:
                    values[metric.name].append(metric.value)
            for name in sorted(values):
                entries = values[name]
                metrics.append(
                    BenchmarkMetric(
                        name=name,
                        value=round(sum(entries) / len(entries), 6),
                        suite_name=suite_name,
                        description=f"Mean of {len(entries)} case-level values",
                        metadata={"case_values": entries},
                    )
                )
        return metrics
