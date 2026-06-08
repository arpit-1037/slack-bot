"""Append-only benchmark run storage and trend comparison."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tests.evaluation.benchmark_models import BenchmarkReport


class BenchmarkStore:
    """Persist benchmark reports as JSON Lines for portable CI use."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        configured = storage_path or os.getenv(
            "BENCHMARK_STORE_PATH",
            ".benchmark_runs/benchmark_runs.jsonl",
        )
        self.storage_path = Path(configured).expanduser()

    def save_report(self, report: BenchmarkReport) -> Path:
        """Append one benchmark report and return the storage path."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.storage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report.as_dict(), sort_keys=True) + "\n")
        return self.storage_path

    def load_reports(self, limit: int | None = None) -> list[BenchmarkReport]:
        """Load valid reports in historical order."""
        if not self.storage_path.exists():
            return []
        reports: list[BenchmarkReport] = []
        with self.storage_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    reports.append(BenchmarkReport.from_dict(payload))
        if limit is not None:
            return reports[-max(limit, 0):]
        return reports

    def latest_report(self) -> BenchmarkReport | None:
        """Return the newest persisted report."""
        reports = self.load_reports(limit=1)
        return reports[0] if reports else None

    def compare_runs(
        self,
        current: BenchmarkReport,
        baseline: BenchmarkReport | None = None,
    ) -> dict[str, Any]:
        """Compare aggregate metrics between two runs."""
        baseline = baseline or self.latest_report()
        if baseline is None or baseline.run_id == current.run_id:
            return {"baseline_run_id": None, "current_run_id": current.run_id, "deltas": {}}

        baseline_metrics = {
            (metric.suite_name, metric.name): metric.value
            for metric in baseline.metrics
        }
        current_metrics = {
            (metric.suite_name, metric.name): metric.value
            for metric in current.metrics
        }
        deltas = {}
        for key in sorted(set(baseline_metrics) | set(current_metrics)):
            suite_name, metric_name = key
            before = baseline_metrics.get(key, 0.0)
            after = current_metrics.get(key, 0.0)
            deltas[f"{suite_name}:{metric_name}".lstrip(":")] = {
                "baseline": before,
                "current": after,
                "delta": round(after - before, 6),
                "regression": after < before,
            }
        return {
            "baseline_run_id": baseline.run_id,
            "current_run_id": current.run_id,
            "deltas": deltas,
        }

    def historical_trends(self, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        """Return metric values grouped across recent runs."""
        trends: dict[str, list[dict[str, Any]]] = {}
        for report in self.load_reports(limit=limit):
            for metric in report.metrics:
                key = f"{metric.suite_name}:{metric.name}".lstrip(":")
                trends.setdefault(key, []).append(
                    {
                        "run_id": report.run_id,
                        "completed_at": report.completed_at,
                        "value": metric.value,
                    }
                )
        return trends
