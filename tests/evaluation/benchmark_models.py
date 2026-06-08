"""Structured models shared by all benchmark components."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class BenchmarkContext:
    """Runtime context supplied to one benchmark evaluator."""

    run_id: str
    project_path: str
    suite_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkObservation:
    """Raw evaluator output before it is converted into a benchmark result."""

    actual_output: Any
    passed: bool | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    failure_message: str = ""
    failure_category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


BenchmarkEvaluator = Callable[["BenchmarkCase", BenchmarkContext], BenchmarkObservation]


@dataclass(frozen=True)
class BenchmarkCase:
    """One deterministic input and expected output pair."""

    id: str
    name: str
    input_data: Any
    expected_output: Any
    category: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluator: BenchmarkEvaluator | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation without callable fields."""
        return {
            "id": self.id,
            "name": self.name,
            "input_data": _json_safe(self.input_data),
            "expected_output": _json_safe(self.expected_output),
            "category": self.category,
            "tags": list(self.tags),
            "metadata": _json_safe(self.metadata),
        }


@dataclass(frozen=True)
class BenchmarkMetric:
    """One named score emitted by a case, suite, or full run."""

    name: str
    value: float
    unit: str = "ratio"
    suite_name: str = ""
    case_id: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe metric dictionary."""
        return _json_safe(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkMetric":
        """Build a metric from persisted data."""
        return cls(
            name=str(data.get("name") or ""),
            value=float(data.get("value") or 0.0),
            unit=str(data.get("unit") or "ratio"),
            suite_name=str(data.get("suite_name") or ""),
            case_id=str(data.get("case_id") or ""),
            description=str(data.get("description") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BenchmarkFailure:
    """Structured failure information for one benchmark case."""

    suite_name: str
    case_id: str
    case_name: str
    category: str
    message: str
    expected: Any = None
    actual: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe failure dictionary."""
        return _json_safe(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkFailure":
        """Build a failure from persisted data."""
        return cls(
            suite_name=str(data.get("suite_name") or ""),
            case_id=str(data.get("case_id") or ""),
            case_name=str(data.get("case_name") or ""),
            category=str(data.get("category") or "assertion"),
            message=str(data.get("message") or ""),
            expected=data.get("expected"),
            actual=data.get("actual"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BenchmarkResult:
    """Completed result for one benchmark case."""

    run_id: str
    suite_name: str
    case_id: str
    case_name: str
    passed: bool
    expected_output: Any
    actual_output: Any
    duration_seconds: float
    metrics: tuple[BenchmarkMetric, ...] = field(default_factory=tuple)
    failure: BenchmarkFailure | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def metric_values(self) -> dict[str, float]:
        """Return case metrics keyed by name."""
        return {metric.name: metric.value for metric in self.metrics}

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe result dictionary."""
        return {
            "run_id": self.run_id,
            "suite_name": self.suite_name,
            "case_id": self.case_id,
            "case_name": self.case_name,
            "passed": self.passed,
            "expected_output": _json_safe(self.expected_output),
            "actual_output": _json_safe(self.actual_output),
            "duration_seconds": self.duration_seconds,
            "metrics": [metric.as_dict() for metric in self.metrics],
            "failure": self.failure.as_dict() if self.failure else None,
            "metadata": _json_safe(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkResult":
        """Build a result from persisted data."""
        failure_data = data.get("failure")
        return cls(
            run_id=str(data.get("run_id") or ""),
            suite_name=str(data.get("suite_name") or ""),
            case_id=str(data.get("case_id") or ""),
            case_name=str(data.get("case_name") or ""),
            passed=bool(data.get("passed")),
            expected_output=data.get("expected_output"),
            actual_output=data.get("actual_output"),
            duration_seconds=float(data.get("duration_seconds") or 0.0),
            metrics=tuple(
                BenchmarkMetric.from_dict(item)
                for item in data.get("metrics", [])
                if isinstance(item, Mapping)
            ),
            failure=(
                BenchmarkFailure.from_dict(failure_data)
                if isinstance(failure_data, Mapping)
                else None
            ),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class BenchmarkSuite:
    """A named collection of benchmark cases with one evaluator."""

    name: str
    description: str
    cases: list[BenchmarkCase] = field(default_factory=list)
    evaluator: BenchmarkEvaluator | None = field(default=None, repr=False, compare=False)
    metric_names: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_case(self, case: BenchmarkCase) -> None:
        """Register a case on this suite, rejecting duplicate ids."""
        if any(existing.id == case.id for existing in self.cases):
            raise ValueError(f"Benchmark case already registered in {self.name}: {case.id}")
        self.cases.append(case)

    def as_dict(self) -> dict[str, Any]:
        """Return suite metadata and cases without callable fields."""
        return {
            "name": self.name,
            "description": self.description,
            "cases": [case.as_dict() for case in self.cases],
            "metric_names": list(self.metric_names),
            "tags": list(self.tags),
            "metadata": _json_safe(self.metadata),
        }


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregated report for one complete benchmark run."""

    run_id: str
    started_at: str
    completed_at: str
    duration_seconds: float
    suite_names: tuple[str, ...]
    results: tuple[BenchmarkResult, ...]
    metrics: tuple[BenchmarkMetric, ...]
    failures: tuple[BenchmarkFailure, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_cases(self) -> int:
        """Return the number of executed benchmark cases."""
        return len(self.results)

    @property
    def passed_cases(self) -> int:
        """Return the number of passing cases."""
        return sum(1 for result in self.results if result.passed)

    @property
    def failed_cases(self) -> int:
        """Return the number of failing cases."""
        return self.total_cases - self.passed_cases

    @property
    def pass_rate(self) -> float:
        """Return the overall pass ratio."""
        if not self.results:
            return 0.0
        return self.passed_cases / self.total_cases

    def metric_value(self, name: str, suite_name: str = "") -> float | None:
        """Return one aggregate metric value when present."""
        for metric in self.metrics:
            if metric.name == name and metric.suite_name == suite_name:
                return metric.value
        return None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe report dictionary."""
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "suite_names": list(self.suite_names),
            "results": [result.as_dict() for result in self.results],
            "metrics": [metric.as_dict() for metric in self.metrics],
            "failures": [failure.as_dict() for failure in self.failures],
            "metadata": _json_safe(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkReport":
        """Build a report from persisted JSON data."""
        return cls(
            run_id=str(data.get("run_id") or ""),
            started_at=str(data.get("started_at") or ""),
            completed_at=str(data.get("completed_at") or ""),
            duration_seconds=float(data.get("duration_seconds") or 0.0),
            suite_names=tuple(str(item) for item in data.get("suite_names", [])),
            results=tuple(
                BenchmarkResult.from_dict(item)
                for item in data.get("results", [])
                if isinstance(item, Mapping)
            ),
            metrics=tuple(
                BenchmarkMetric.from_dict(item)
                for item in data.get("metrics", [])
                if isinstance(item, Mapping)
            ),
            failures=tuple(
                BenchmarkFailure.from_dict(item)
                for item in data.get("failures", [])
                if isinstance(item, Mapping)
            ),
            metadata=dict(data.get("metadata") or {}),
        )


def _json_safe(value: Any) -> Any:
    """Convert common structured values into JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _json_safe(value.as_dict())
    return repr(value)
