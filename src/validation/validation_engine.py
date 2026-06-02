"""Main orchestrator for validation and verification."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Mapping

from src.modification.modification_models import CodePatch
from src.repository.repository_indexer import RepositoryIndexer
from src.utils.helpers import get_logger
from src.validation.import_checker import ImportChecker
from src.validation.lint_runner import LintRunner
from src.validation.syntax_validator import SyntaxValidator
from src.validation.test_runner import TestRunner
from src.validation.validation_models import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARNING,
    ImportCheckResult,
    LintResult,
    SyntaxCheckResult,
    TestExecutionResult,
    ValidationIssue,
    ValidationReport,
    ValidationResult,
)
from src.validation.validation_reporter import ValidationReporter

log = get_logger(__name__)


class ValidationEngine:
    """Coordinate syntax, import, test, lint, and report generation."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        syntax_validator: SyntaxValidator | None = None,
        import_checker: ImportChecker | None = None,
        test_runner: TestRunner | None = None,
        lint_runner: LintRunner | None = None,
        reporter: ValidationReporter | None = None,
        run_tests_by_default: bool = True,
        run_lint_by_default: bool = True,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.syntax_validator = syntax_validator or SyntaxValidator()
        self.import_checker = import_checker or ImportChecker(indexer=self.indexer)
        self.test_runner = test_runner or TestRunner()
        self.lint_runner = lint_runner or LintRunner()
        self.reporter = reporter or ValidationReporter()
        self.run_tests_by_default = run_tests_by_default
        self.run_lint_by_default = run_lint_by_default

    def validate_patch(
        self,
        patch: CodePatch,
        project_path: str,
        run_tests: bool | None = None,
        run_lint: bool | None = None,
        request_id: str | None = None,
    ) -> ValidationReport:
        """Validate a proposed patch without modifying the original repository."""
        start = time.monotonic()
        project_path = os.path.abspath(os.path.expanduser(project_path))
        proposed_files = self._proposed_files(patch)
        syntax = self.syntax_validator.validate_files(proposed_files)
        imports = self.import_checker.validate_imports(project_path, proposed_files)

        test_result: TestExecutionResult | None = None
        lint_result: LintResult | None = None
        should_run_tests = self.run_tests_by_default if run_tests is None else run_tests
        should_run_lint = self.run_lint_by_default if run_lint is None else run_lint

        overlay_issue: ValidationIssue | None = None
        if should_run_tests or should_run_lint:
            try:
                with tempfile.TemporaryDirectory(prefix="slack-claude-validation-") as tmp:
                    temp_project = self._copy_repository(project_path, tmp)
                    self._apply_patch_overlay(temp_project, patch)
                    if should_run_tests:
                        test_result = self.test_runner.run_tests(temp_project)
                    if should_run_lint:
                        lint_result = self.lint_runner.run_linting(temp_project, patch.affected_paths)
            except Exception as error:
                log.exception("request_id=%s validation overlay failed", request_id)
                overlay_issue = ValidationIssue(
                    file_path="repository",
                    message=f"Could not create validation overlay: {error}",
                    check="validation-overlay",
                )

        report = self._build_report(
            start=start,
            syntax=syntax,
            imports=imports,
            tests=test_result,
            lint=lint_result,
            extra_errors=[overlay_issue] if overlay_issue else [],
            metadata={"request_id": request_id, "affected_paths": patch.affected_paths},
        )
        log.info(
            "request_id=%s patch validation status=%s confidence=%.2f files=%d",
            request_id,
            report.status,
            report.confidence_score,
            len(patch.affected_paths),
        )
        return report

    def validate_repository(
        self,
        project_path: str,
        run_tests: bool | None = None,
        run_lint: bool | None = None,
        request_id: str | None = None,
    ) -> ValidationReport:
        """Validate current repository health."""
        start = time.monotonic()
        project_path = os.path.abspath(os.path.expanduser(project_path))
        index = self.indexer.ensure_index(project_path)
        files = {
            path: entry.get("content", "")
            for path, entry in index.items()
        }
        syntax = self.syntax_validator.validate_files(files)
        imports = self.import_checker.validate_imports(project_path, None)
        should_run_tests = self.run_tests_by_default if run_tests is None else run_tests
        should_run_lint = self.run_lint_by_default if run_lint is None else run_lint
        tests = self.test_runner.run_tests(project_path) if should_run_tests else None
        lint = self.lint_runner.run_linting(project_path) if should_run_lint else None
        report = self._build_report(
            start=start,
            syntax=syntax,
            imports=imports,
            tests=tests,
            lint=lint,
            metadata={"request_id": request_id, "file_count": len(files)},
        )
        log.info(
            "request_id=%s repository validation status=%s confidence=%.2f files=%d",
            request_id,
            report.status,
            report.confidence_score,
            len(files),
        )
        return report

    def generate_report(self, report: ValidationReport) -> str:
        """Return a human-readable validation report."""
        return self.reporter.build_report(report)

    def _build_report(
        self,
        start: float,
        syntax: SyntaxCheckResult | None = None,
        imports: ImportCheckResult | None = None,
        tests: TestExecutionResult | None = None,
        lint: LintResult | None = None,
        extra_errors: list[ValidationIssue] | None = None,
        metadata: dict | None = None,
    ) -> ValidationReport:
        results = [result for result in (syntax, imports, tests, lint) if result is not None]
        errors = [
            issue
            for result in results
            for issue in result.errors
        ]
        warnings = [
            issue
            for result in results
            for issue in result.warnings
        ]
        errors.extend(extra_errors or [])
        status = self._aggregate_status(results, errors, warnings)
        confidence = self._aggregate_confidence(results, errors, warnings)
        summary = self.reporter.summarize_results(
            syntax=syntax,
            imports=imports,
            tests=tests,
            lint=lint,
        )
        report = ValidationReport(
            status=status,
            summary=summary,
            confidence_score=confidence,
            execution_time_seconds=round(time.monotonic() - start, 4),
            syntax=syntax,
            imports=imports,
            tests=tests,
            lint=lint,
            errors=errors,
            warnings=warnings,
            metadata=metadata or {},
        )
        return ValidationReport(
            status=report.status,
            summary=report.summary,
            confidence_score=report.confidence_score,
            execution_time_seconds=report.execution_time_seconds,
            syntax=report.syntax,
            imports=report.imports,
            tests=report.tests,
            lint=report.lint,
            errors=report.errors,
            warnings=report.warnings,
            report_text=self.reporter.build_report(report),
            metadata=report.metadata,
        )

    def _aggregate_status(
        self,
        results: list[ValidationResult],
        errors: list[ValidationIssue],
        warnings: list[ValidationIssue],
    ) -> str:
        if errors or any(result.status == STATUS_FAIL for result in results):
            return STATUS_FAIL
        if warnings or any(result.status == STATUS_WARNING for result in results):
            return STATUS_WARNING
        return STATUS_PASS

    def _aggregate_confidence(
        self,
        results: list[ValidationResult],
        errors: list[ValidationIssue],
        warnings: list[ValidationIssue],
    ) -> float:
        if not results:
            return 0.5
        score = sum(result.confidence_score for result in results) / len(results)
        score -= len(errors) * 0.05
        score -= len(warnings) * 0.02
        return max(0.0, min(1.0, round(score, 2)))

    def _proposed_files(self, patch: CodePatch) -> dict[str, str | None]:
        return {
            change.file_path.replace(os.sep, "/"): change.new_content
            for change in patch.changes
        }

    def _copy_repository(self, project_path: str, temp_root: str) -> str:
        source = Path(project_path)
        destination = Path(temp_root) / "project"
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".venv",
                "venv",
                "node_modules",
            ),
        )
        return str(destination)

    def _apply_patch_overlay(self, temp_project: str, patch: CodePatch) -> None:
        root = Path(temp_project).resolve()
        for change in patch.changes:
            relative = Path(change.file_path)
            target = (root / relative).resolve(strict=False)
            if target != root and root not in target.parents:
                raise ValueError(f"Patch path escapes repository root: {change.file_path}")
            if change.new_content is None:
                if target.exists():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.new_content, encoding="utf-8")
