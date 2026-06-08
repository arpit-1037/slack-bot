"""Tests for benchmark orchestration, reporting, storage, and built-in suites."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkObservation,
    BenchmarkSuite,
)
from tests.evaluation.benchmark_registry import BenchmarkRegistry
from tests.evaluation.benchmark_reporter import BenchmarkReporter
from tests.evaluation.benchmark_runner import BenchmarkRunner, create_default_registry
from tests.evaluation.benchmark_store import BenchmarkStore


class BenchmarkRegistryTest(unittest.TestCase):
    """Registry behavior remains deterministic and rejects duplicates."""

    def test_registers_suites_and_cases(self) -> None:
        registry = BenchmarkRegistry()
        suite = registry.register_suite(
            BenchmarkSuite(name="example", description="Example suite")
        )
        registry.register_case(
            "example",
            BenchmarkCase(
                id="case-1",
                name="Example",
                input_data="input",
                expected_output="output",
            ),
        )

        self.assertIs(registry.get_suite("example"), suite)
        self.assertEqual([case.id for case in suite.cases], ["case-1"])
        self.assertEqual([item.name for item in registry.list_suites()], ["example"])

    def test_rejects_duplicate_suite_and_case_ids(self) -> None:
        registry = BenchmarkRegistry()
        suite = registry.register_suite(
            BenchmarkSuite(name="example", description="Example suite")
        )
        case = BenchmarkCase(
            id="case-1",
            name="Example",
            input_data="input",
            expected_output="output",
        )
        suite.add_case(case)

        with self.assertRaises(ValueError):
            registry.register_suite(
                BenchmarkSuite(name="example", description="Duplicate")
            )
        with self.assertRaises(ValueError):
            suite.add_case(case)


class BenchmarkRunnerTest(unittest.TestCase):
    """Runner isolates failures and aggregates case metrics."""

    def test_runs_pass_fail_and_exception_cases(self) -> None:
        def evaluator(case, context):
            if case.id == "error":
                raise RuntimeError("broken evaluator")
            passed = case.input_data == case.expected_output
            return BenchmarkObservation(
                actual_output=case.input_data,
                passed=passed,
                metrics={"routing_accuracy": float(passed)},
                failure_category="route_mismatch",
            )

        suite = BenchmarkSuite(
            name="routing",
            description="Test routing",
            evaluator=evaluator,
            cases=[
                BenchmarkCase("pass", "Pass", "git", "git"),
                BenchmarkCase("fail", "Fail", "general", "git"),
                BenchmarkCase("error", "Error", "input", "output"),
            ],
        )
        registry = BenchmarkRegistry()
        registry.register_suite(suite)
        runner = BenchmarkRunner(registry=registry, project_path=".")

        report = runner.run_all_benchmarks(persist=False, run_id="benchmark-test")

        self.assertEqual(report.total_cases, 3)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 2)
        self.assertEqual(
            {failure.category for failure in report.failures},
            {"route_mismatch", "evaluator_error"},
        )
        self.assertAlmostEqual(
            report.metric_value("overall_accuracy") or 0.0,
            1 / 3,
            places=6,
        )


class BenchmarkStoreReporterTest(unittest.TestCase):
    """Reports round-trip through JSONL storage and expose regressions."""

    def test_persists_compares_and_formats_reports(self) -> None:
        suite = BenchmarkSuite(
            name="example",
            description="Example",
            evaluator=lambda case, context: BenchmarkObservation(
                actual_output=case.input_data,
                passed=case.input_data == case.expected_output,
                metrics={"example_accuracy": float(case.input_data == case.expected_output)},
                failure_category="example_failure",
            ),
            cases=[BenchmarkCase("case", "Example case", "actual", "expected")],
        )
        registry = BenchmarkRegistry()
        registry.register_suite(suite)

        with tempfile.TemporaryDirectory() as tmp:
            store = BenchmarkStore(Path(tmp) / "runs.jsonl")
            runner = BenchmarkRunner(registry=registry, store=store)
            first = runner.run_all_benchmarks(run_id="run-1")
            second = runner.run_all_benchmarks(run_id="run-2")

            loaded = store.load_reports()
            comparison = store.compare_runs(second, baseline=first)
            formatted = BenchmarkReporter().generate_report(second)

            self.assertEqual([report.run_id for report in loaded], ["run-1", "run-2"])
            self.assertEqual(comparison["baseline_run_id"], "run-1")
            self.assertIn("Failing Cases", formatted)
            self.assertIn("Most affected suite", formatted)


class BuiltInBenchmarkTest(unittest.TestCase):
    """Built-in suites are complete and lightweight subsets execute."""

    def test_default_registry_contains_all_requested_suites(self) -> None:
        names = {suite.name for suite in create_default_registry().list_suites()}

        self.assertEqual(
            names,
            {
                "routing",
                "retrieval",
                "memory",
                "workflow",
                "conversation",
                "git",
                "planner",
                "execution",
            },
        )

    def test_deterministic_non_io_suites_execute(self) -> None:
        runner = BenchmarkRunner(project_path=".")

        report = runner.run_all_benchmarks(
            suite_names=["routing", "memory", "workflow", "conversation"],
            persist=False,
            run_id="built-in-smoke",
        )

        self.assertGreater(report.total_cases, 0)
        self.assertTrue(any(metric.name == "routing_accuracy" for metric in report.metrics))
        self.assertFalse(
            any(failure.category == "evaluator_error" for failure in report.failures)
        )


if __name__ == "__main__":
    unittest.main()
