"""Human-readable validation report generation."""

from __future__ import annotations

from src.validation.validation_models import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    STATUS_WARNING,
    ImportCheckResult,
    LintResult,
    SyntaxCheckResult,
    TestExecutionResult,
    ValidationReport,
    ValidationResult,
)


class ValidationReporter:
    """Build concise validation reports for Slack or CLI display."""

    def build_report(self, report: ValidationReport) -> str:
        """Return a human-readable report from aggregate validation data."""
        lines = [
            "Validation Summary",
            "",
            self._check_line("Syntax", report.syntax),
            self._check_line("Imports", report.imports),
            self._check_line("Tests", report.tests),
            self._check_line("Lint", report.lint),
            "",
            f"Confidence Score: {int(round(report.confidence_score * 100))}%",
        ]
        if report.errors:
            lines.extend(["", "Errors:"])
            lines.extend(f"- {issue.format()}" for issue in report.errors[:8])
        if report.warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {issue.format()}" for issue in report.warnings[:8])
        return "\n".join(lines)

    def summarize_results(
        self,
        syntax: SyntaxCheckResult | None = None,
        imports: ImportCheckResult | None = None,
        tests: TestExecutionResult | None = None,
        lint: LintResult | None = None,
    ) -> str:
        """Return one-line summaries for supplied validation results."""
        return "; ".join(
            result.summary
            for result in (syntax, imports, tests, lint)
            if result is not None and result.summary
        )

    def _check_line(self, label: str, result: ValidationResult | None) -> str:
        if result is None:
            return f"[SKIP] {label}: not run"
        marker = self._marker(result.status)
        return f"{marker} {label}: {result.summary}"

    def _marker(self, status: str) -> str:
        if status == STATUS_PASS:
            return "[PASS]"
        if status == STATUS_FAIL:
            return "[FAIL]"
        if status == STATUS_WARNING:
            return "[WARN]"
        if status == STATUS_SKIPPED:
            return "[SKIP]"
        return "[INFO]"
