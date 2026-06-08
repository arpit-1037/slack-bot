"""Benchmark execution orchestration and CI-friendly command line interface."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.utils.helpers import get_logger
from tests.evaluation.benchmark_metrics import BenchmarkMetrics
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkFailure,
    BenchmarkMetric,
    BenchmarkObservation,
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkSuite,
)
from tests.evaluation.benchmark_registry import BenchmarkRegistry
from tests.evaluation.benchmark_reporter import BenchmarkReporter
from tests.evaluation.benchmark_store import BenchmarkStore

log = get_logger(__name__)


class BenchmarkRunner:
    """Execute benchmark suites with structured logging and failure isolation."""

    def __init__(
        self,
        registry: BenchmarkRegistry | None = None,
        metrics: BenchmarkMetrics | None = None,
        reporter: BenchmarkReporter | None = None,
        store: BenchmarkStore | None = None,
        project_path: str | None = None,
    ) -> None:
        self.project_path = str(Path(project_path or ".").expanduser().resolve())
        self.registry = registry or create_default_registry()
        self.metrics = metrics or BenchmarkMetrics()
        self.reporter = reporter or BenchmarkReporter()
        self.store = store or BenchmarkStore()

    def run_all_benchmarks(
        self,
        suite_names: Iterable[str] | None = None,
        persist: bool = True,
        run_id: str | None = None,
    ) -> BenchmarkReport:
        """Execute selected suites and return one aggregate report."""
        selected = self._selected_suites(suite_names)
        resolved_run_id = run_id or self._new_run_id()
        started_at = self._utc_now()
        started = time.monotonic()
        log.info(
            "benchmark_run_id=%s suites=%s project_path=%s",
            resolved_run_id,
            ",".join(suite.name for suite in selected),
            self.project_path,
        )

        results: list[BenchmarkResult] = []
        for suite in selected:
            results.extend(self.run_suite(suite, resolved_run_id))

        completed_at = self._utc_now()
        aggregate_metrics = self.metrics.aggregate_results(results)
        failures = tuple(
            result.failure
            for result in results
            if result.failure is not None
        )
        report = BenchmarkReport(
            run_id=resolved_run_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=round(time.monotonic() - started, 6),
            suite_names=tuple(suite.name for suite in selected),
            results=tuple(results),
            metrics=tuple(aggregate_metrics),
            failures=failures,
            metadata={
                "project_path": self.project_path,
                "python_hash_seed": os.getenv("PYTHONHASHSEED", ""),
            },
        )

        baseline = self.store.latest_report() if persist else None
        if baseline is not None:
            comparison = self.store.compare_runs(report, baseline=baseline)
            report = replace(report, metadata={**report.metadata, "comparison": comparison})
        if persist:
            storage_path = self.store.save_report(report)
            report = replace(
                report,
                metadata={**report.metadata, "storage_path": str(storage_path)},
            )

        log.info(
            "benchmark_run_id=%s complete pass_rate=%.4f failures=%d duration=%.4f",
            report.run_id,
            report.pass_rate,
            report.failed_cases,
            report.duration_seconds,
        )
        return report

    def run_suite(self, suite: BenchmarkSuite, run_id: str) -> list[BenchmarkResult]:
        """Execute one suite and isolate failures to individual cases."""
        started = time.monotonic()
        log.info(
            "benchmark_run_id=%s suite=%s cases=%d start",
            run_id,
            suite.name,
            len(suite.cases),
        )
        results = [self.run_case(suite, case, run_id) for case in suite.cases]
        pass_rate = (
            sum(1 for result in results if result.passed) / len(results)
            if results
            else 0.0
        )
        log.info(
            "benchmark_run_id=%s suite=%s pass_rate=%.4f failures=%d duration=%.4f",
            run_id,
            suite.name,
            pass_rate,
            sum(1 for result in results if not result.passed),
            time.monotonic() - started,
        )
        return results

    def run_case(
        self,
        suite: BenchmarkSuite,
        case: BenchmarkCase,
        run_id: str,
    ) -> BenchmarkResult:
        """Execute one case and convert evaluator exceptions into failures."""
        started = time.monotonic()
        context = BenchmarkContext(
            run_id=run_id,
            project_path=self.project_path,
            suite_name=suite.name,
            metadata={"suite_metadata": dict(suite.metadata)},
        )
        evaluator = case.evaluator or suite.evaluator
        try:
            observation = (
                evaluator(case, context)
                if evaluator is not None
                else BenchmarkObservation(actual_output=case.input_data)
            )
            if not isinstance(observation, BenchmarkObservation):
                raise TypeError("Benchmark evaluator must return BenchmarkObservation.")
            passed = (
                observation.passed
                if observation.passed is not None
                else observation.actual_output == case.expected_output
            )
            failure = self._failure_for_observation(
                suite,
                case,
                observation,
                bool(passed),
            )
            metrics = tuple(
                BenchmarkMetric(
                    name=name,
                    value=float(value),
                    suite_name=suite.name,
                    case_id=case.id,
                )
                for name, value in sorted(observation.metrics.items())
            )
            return BenchmarkResult(
                run_id=run_id,
                suite_name=suite.name,
                case_id=case.id,
                case_name=case.name,
                passed=bool(passed),
                expected_output=case.expected_output,
                actual_output=observation.actual_output,
                duration_seconds=round(time.monotonic() - started, 6),
                metrics=metrics,
                failure=failure,
                metadata={**case.metadata, **observation.metadata},
            )
        except Exception as error:
            log.exception(
                "benchmark_run_id=%s suite=%s case=%s evaluator_error",
                run_id,
                suite.name,
                case.id,
            )
            failure = BenchmarkFailure(
                suite_name=suite.name,
                case_id=case.id,
                case_name=case.name,
                category="evaluator_error",
                message=f"{type(error).__name__}: {error}",
                expected=case.expected_output,
                actual=None,
                metadata={"case_category": case.category},
            )
            return BenchmarkResult(
                run_id=run_id,
                suite_name=suite.name,
                case_id=case.id,
                case_name=case.name,
                passed=False,
                expected_output=case.expected_output,
                actual_output=None,
                duration_seconds=round(time.monotonic() - started, 6),
                metrics=tuple(
                    BenchmarkMetric(
                        name=name,
                        value=0.0,
                        suite_name=suite.name,
                        case_id=case.id,
                    )
                    for name in sorted(suite.metric_names)
                ),
                failure=failure,
                metadata=dict(case.metadata),
            )

    def _failure_for_observation(
        self,
        suite: BenchmarkSuite,
        case: BenchmarkCase,
        observation: BenchmarkObservation,
        passed: bool,
    ) -> BenchmarkFailure | None:
        if passed:
            return None
        message = observation.failure_message or (
            f"Expected {case.expected_output!r}, received {observation.actual_output!r}."
        )
        return BenchmarkFailure(
            suite_name=suite.name,
            case_id=case.id,
            case_name=case.name,
            category=observation.failure_category or case.category or "assertion",
            message=message,
            expected=case.expected_output,
            actual=observation.actual_output,
            metadata={**case.metadata, **observation.metadata},
        )

    def _selected_suites(
        self,
        suite_names: Iterable[str] | None,
    ) -> list[BenchmarkSuite]:
        if suite_names is None:
            return self.registry.list_suites()
        return [self.registry.get_suite(name) for name in suite_names]

    def _new_run_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"benchmark-{stamp}-{time.time_ns() % 1_000_000_000:09d}"

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


def create_default_registry() -> BenchmarkRegistry:
    """Create a fresh registry containing every built-in suite."""
    from tests.evaluation.conversation_tests import create_conversation_suite
    from tests.evaluation.execution_tests import create_execution_suite
    from tests.evaluation.git_tests import create_git_suite
    from tests.evaluation.memory_tests import create_memory_suite
    from tests.evaluation.planner_tests import create_planner_suite
    from tests.evaluation.retrieval_tests import create_retrieval_suite
    from tests.evaluation.routing_tests import create_routing_suite
    from tests.evaluation.workflow_tests import create_workflow_suite

    registry = BenchmarkRegistry()
    for factory in (
        create_routing_suite,
        create_retrieval_suite,
        create_memory_suite,
        create_workflow_suite,
        create_conversation_suite,
        create_git_suite,
        create_planner_suite,
        create_execution_suite,
    ):
        registry.register_suite(factory())
    return registry


def run_all_benchmarks(
    project_path: str | None = None,
    suite_names: Iterable[str] | None = None,
    store_path: str | Path | None = None,
    persist: bool = True,
) -> BenchmarkReport:
    """Run all built-in benchmarks through a fresh runner."""
    runner = BenchmarkRunner(
        project_path=project_path,
        store=BenchmarkStore(store_path),
    )
    return runner.run_all_benchmarks(suite_names=suite_names, persist=persist)


def run_admin_benchmark_command(
    command: str,
    slack_user: str,
    approved_users: Iterable[str],
    project_path: str | None = None,
) -> str | None:
    """Handle the optional approval-gated `run benchmarks` admin command."""
    if " ".join(command.lower().split()) != "run benchmarks":
        return None
    if slack_user not in set(approved_users):
        return "You are not approved to run repository benchmarks."
    report = run_all_benchmarks(project_path=project_path)
    return BenchmarkReporter().generate_report(report)


def main(argv: list[str] | None = None) -> int:
    """Run benchmarks from any CI system and return a threshold-based exit code."""
    parser = argparse.ArgumentParser(description="Run deterministic repository benchmarks.")
    parser.add_argument("--project-path", default=".")
    parser.add_argument("--suite", action="append", dest="suites")
    parser.add_argument("--store", default=None)
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--fail-under", type=float, default=1.0)
    args = parser.parse_args(argv)

    report = run_all_benchmarks(
        project_path=args.project_path,
        suite_names=args.suites,
        store_path=args.store,
        persist=not args.no_store,
    )
    if args.as_json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(BenchmarkReporter().generate_report(report))
    return 0 if report.pass_rate >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
