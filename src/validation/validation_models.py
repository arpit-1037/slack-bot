"""Typed models for validation and verification workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARNING = "warning"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class ValidationIssue:
    """One validation error or warning."""

    file_path: str
    message: str
    severity: str = "error"
    line: int | None = None
    column: int | None = None
    check: str = ""

    def format(self) -> str:
        """Return a concise human-readable issue line."""
        location = ""
        if self.line is not None:
            location = f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        check = f" {self.check}" if self.check else ""
        return f"[{self.severity}]{check} {self.file_path}{location}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    """Base result shape shared by validation checks."""

    name: str
    status: str
    execution_time_seconds: float = 0.0
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    summary: str = ""
    confidence_score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return True when the check has no blocking errors."""
        return self.status == STATUS_PASS and not self.errors

    @property
    def issue_count(self) -> int:
        """Return the total number of errors and warnings."""
        return len(self.errors) + len(self.warnings)


@dataclass(frozen=True)
class SyntaxCheckResult(ValidationResult):
    """Syntax validation result for source and configuration files."""

    checked_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportCheckResult(ValidationResult):
    """Import validation result for Python files."""

    missing_imports: list[str] = field(default_factory=list)
    circular_imports: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class TestExecutionResult(ValidationResult):
    """Safe test execution result."""

    command: list[str] = field(default_factory=list)
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class LintResult(ValidationResult):
    """Lint execution result."""

    command: list[str] = field(default_factory=list)
    linter: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class ValidationReport:
    """Aggregate validation report for a patch or repository."""

    status: str
    summary: str
    confidence_score: float
    execution_time_seconds: float
    syntax: SyntaxCheckResult | None = None
    imports: ImportCheckResult | None = None
    tests: TestExecutionResult | None = None
    lint: LintResult | None = None
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    report_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return True when the aggregate report has no failing checks."""
        return self.status == STATUS_PASS and not self.errors


def result_status(errors: list[ValidationIssue], warnings: list[ValidationIssue] | None = None) -> str:
    """Return a status from issue lists."""
    if errors:
        return STATUS_FAIL
    if warnings:
        return STATUS_WARNING
    return STATUS_PASS


def confidence_from_issues(
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue] | None = None,
    base: float = 1.0,
) -> float:
    """Calculate a bounded confidence score from errors and warnings."""
    warning_count = len(warnings or [])
    score = base - (len(errors) * 0.25) - (warning_count * 0.08)
    return max(0.0, min(1.0, round(score, 2)))
