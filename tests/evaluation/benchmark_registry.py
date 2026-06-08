"""Registry for benchmark suites and cases."""

from __future__ import annotations

from tests.evaluation.benchmark_models import BenchmarkCase, BenchmarkSuite


class BenchmarkRegistry:
    """Store benchmark suites in deterministic registration order."""

    def __init__(self) -> None:
        self._suites: dict[str, BenchmarkSuite] = {}

    def register_suite(self, suite: BenchmarkSuite, replace: bool = False) -> BenchmarkSuite:
        """Register and return a suite."""
        if suite.name in self._suites and not replace:
            raise ValueError(f"Benchmark suite already registered: {suite.name}")
        self._suites[suite.name] = suite
        return suite

    def register_case(self, suite_name: str, case: BenchmarkCase) -> BenchmarkCase:
        """Register and return a case on an existing suite."""
        suite = self.get_suite(suite_name)
        suite.add_case(case)
        return case

    def get_suite(self, suite_name: str) -> BenchmarkSuite:
        """Return a suite by name."""
        try:
            return self._suites[suite_name]
        except KeyError as error:
            raise KeyError(f"Unknown benchmark suite: {suite_name}") from error

    def list_suites(self) -> list[BenchmarkSuite]:
        """Return suites in stable name order."""
        return [self._suites[name] for name in sorted(self._suites)]

    def clear(self) -> None:
        """Remove all registered suites."""
        self._suites.clear()


_default_registry = BenchmarkRegistry()


def register_suite(suite: BenchmarkSuite, replace: bool = False) -> BenchmarkSuite:
    """Register a suite in the process-wide registry."""
    return _default_registry.register_suite(suite, replace=replace)


def register_case(suite_name: str, case: BenchmarkCase) -> BenchmarkCase:
    """Register a case in the process-wide registry."""
    return _default_registry.register_case(suite_name, case)


def get_suite(suite_name: str) -> BenchmarkSuite:
    """Return a suite from the process-wide registry."""
    return _default_registry.get_suite(suite_name)


def list_suites() -> list[BenchmarkSuite]:
    """List suites from the process-wide registry."""
    return _default_registry.list_suites()


def default_registry() -> BenchmarkRegistry:
    """Return the process-wide benchmark registry."""
    return _default_registry
