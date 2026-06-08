"""Typed models for controlled autonomous analysis workflows."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

WorkflowType = Literal[
    "bug_investigation",
    "authentication_analysis",
    "git_analysis",
    "repository_exploration",
    "architecture_analysis",
    "test_failure_investigation",
    "dependency_investigation",
    "performance_investigation",
]
WorkflowStatus = Literal["pending", "success", "partial", "failure", "skipped"]
WorkflowStepKind = Literal["memory", "repository", "git", "validation", "analysis"]


def stable_workflow_id(*parts: str) -> str:
    """Return a deterministic short id for workflow records."""
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class WorkflowStep:
    """One ordered step in a reusable analysis workflow."""

    id: str
    title: str
    task: str
    kind: WorkflowStepKind
    dependencies: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class Workflow:
    """A reusable controlled autonomous analysis workflow."""

    id: str
    name: str
    workflow_type: WorkflowType
    description: str
    steps: list[WorkflowStep]
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class WorkflowSelection:
    """Selected workflow template and confidence for one task."""

    workflow_type: WorkflowType
    workflow_name: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowValidationResult:
    """Validation result for a workflow."""

    valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowExecution:
    """One execution record for a workflow step."""

    step_id: str
    title: str
    status: WorkflowStatus
    execution_summary: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    files_examined: list[str] = field(default_factory=list)
    execution_time_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class WorkflowResult:
    """Structured result for a completed workflow."""

    workflow_id: str
    workflow_name: str
    workflow_type: WorkflowType
    task: str
    status: WorkflowStatus
    executions: list[WorkflowExecution] = field(default_factory=list)
    files_examined: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    issues_found: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class WorkflowSummary:
    """Slack-ready workflow summary."""

    result: WorkflowResult
    report: str

    def format_markdown(self) -> str:
        """Return the Slack-ready workflow report."""
        return self.report
