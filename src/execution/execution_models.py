"""Typed models for safe read-only plan execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ExecutionStatus = Literal["pending", "success", "partial", "failure", "skipped"]


@dataclass(frozen=True)
class ToolExecutionRequest:
    """One read-only tool call requested by an execution step."""

    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class ExecutionStep:
    """One ordered, read-only execution step derived from a planning step."""

    id: str
    title: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    tool_requests: list[ToolExecutionRequest] = field(default_factory=list)
    source_plan_step_id: str = ""
    expected_outcome: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class ExecutionPlan:
    """A validated set of read-only execution steps for a planning result."""

    id: str
    goal: str
    project_path: str
    steps: list[ExecutionStep]
    source_plan_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class ExecutionValidationResult:
    """Validation outcome for a read-only execution plan."""

    valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionResult:
    """Structured result for one execution step."""

    step_id: str
    title: str
    status: ExecutionStatus
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Return True when the step completed without failed required tools."""
        return self.status == "success"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class ExecutionSummary:
    """Aggregated output for a completed read-only execution plan."""

    execution_id: str
    plan_id: str
    goal: str
    status: ExecutionStatus
    results: list[ExecutionResult] = field(default_factory=list)
    files_examined: list[str] = field(default_factory=list)
    commits_reviewed: list[str] = field(default_factory=list)
    issues_found: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    tools_executed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    findings_report: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)

    def format_markdown(self) -> str:
        """Return the Slack-ready findings report."""
        return self.findings_report
