"""Syntax validation for source and configuration files."""

from __future__ import annotations

import ast
import configparser
import importlib
import json
import time
from pathlib import Path

from src.utils.helpers import get_logger
from src.validation.validation_models import (
    STATUS_SKIPPED,
    SyntaxCheckResult,
    ValidationIssue,
    confidence_from_issues,
    result_status,
)

log = get_logger(__name__)


class SyntaxValidator:
    """Validate Python, JSON, YAML, and common configuration syntax."""

    PYTHON_EXTENSIONS = {".py"}
    JSON_EXTENSIONS = {".json"}
    YAML_EXTENSIONS = {".yaml", ".yml"}
    INI_EXTENSIONS = {".ini", ".cfg"}
    ENV_NAMES = {".env.example"}

    def validate_python(self, content: str, file_path: str = "<memory>") -> SyntaxCheckResult:
        """Validate Python syntax using the standard AST parser."""
        start = time.monotonic()
        errors: list[ValidationIssue] = []
        try:
            tree = ast.parse(content, filename=file_path)
            compile(tree, file_path, "exec")
        except SyntaxError as error:
            errors.append(
                ValidationIssue(
                    file_path=file_path,
                    message=error.msg,
                    line=error.lineno,
                    column=error.offset,
                    check="python-syntax",
                )
            )
        except Exception as error:
            errors.append(
                ValidationIssue(
                    file_path=file_path,
                    message=str(error),
                    check="python-compile",
                )
            )
        return self._result("syntax", [file_path], errors, [], start)

    def validate_json(self, content: str, file_path: str = "<memory>") -> SyntaxCheckResult:
        """Validate JSON syntax using the standard JSON parser."""
        start = time.monotonic()
        errors: list[ValidationIssue] = []
        try:
            json.loads(content or "null")
        except json.JSONDecodeError as error:
            errors.append(
                ValidationIssue(
                    file_path=file_path,
                    message=error.msg,
                    line=error.lineno,
                    column=error.colno,
                    check="json-syntax",
                )
            )
        return self._result("syntax", [file_path], errors, [], start)

    def validate_yaml(self, content: str, file_path: str = "<memory>") -> SyntaxCheckResult:
        """Validate YAML syntax when PyYAML is available."""
        start = time.monotonic()
        warnings: list[ValidationIssue] = []
        errors: list[ValidationIssue] = []
        try:
            yaml = importlib.import_module("yaml")
        except ImportError:
            warnings.append(
                ValidationIssue(
                    file_path=file_path,
                    message="PyYAML is not installed; YAML syntax validation was skipped.",
                    severity="warning",
                    check="yaml-syntax",
                )
            )
            return self._result("syntax", [file_path], errors, warnings, start)

        try:
            yaml.safe_load(content)  # type: ignore[attr-defined]
        except Exception as error:
            line = None
            column = None
            mark = getattr(error, "problem_mark", None)
            if mark is not None:
                line = getattr(mark, "line", 0) + 1
                column = getattr(mark, "column", 0) + 1
            errors.append(
                ValidationIssue(
                    file_path=file_path,
                    message=str(error).splitlines()[0],
                    line=line,
                    column=column,
                    check="yaml-syntax",
                )
            )
        return self._result("syntax", [file_path], errors, warnings, start)

    def validate_file(
        self,
        file_path: str,
        content: str | None = None,
        project_path: str | None = None,
    ) -> SyntaxCheckResult:
        """Validate one file by extension or supported configuration name."""
        start = time.monotonic()
        if content is None:
            content = self._read_file(file_path, project_path)
            if content is None:
                error = ValidationIssue(
                    file_path=file_path,
                    message="File could not be read for syntax validation.",
                    check="syntax-read",
                )
                return self._result("syntax", [file_path], [error], [], start)

        suffix = Path(file_path).suffix.lower()
        name = Path(file_path).name
        if suffix in self.PYTHON_EXTENSIONS:
            return self.validate_python(content, file_path)
        if suffix in self.JSON_EXTENSIONS:
            return self.validate_json(content, file_path)
        if suffix in self.YAML_EXTENSIONS:
            return self.validate_yaml(content, file_path)
        if suffix in self.INI_EXTENSIONS:
            return self._validate_ini(content, file_path, start)
        if name in self.ENV_NAMES:
            return self._validate_env(content, file_path, start)

        return SyntaxCheckResult(
            name="syntax",
            status=STATUS_SKIPPED,
            execution_time_seconds=round(time.monotonic() - start, 4),
            summary=f"No syntax validator configured for {file_path}.",
            confidence_score=0.5,
            checked_files=[],
        )

    def validate_files(self, files: dict[str, str | None]) -> SyntaxCheckResult:
        """Validate a mapping of repository paths to proposed content."""
        start = time.monotonic()
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        checked_files = []
        for file_path, content in sorted(files.items()):
            if content is None:
                continue
            result = self.validate_file(file_path, content)
            checked_files.extend(result.checked_files)
            errors.extend(result.errors)
            warnings.extend(result.warnings)

        return self._result("syntax", sorted(set(checked_files)), errors, warnings, start)

    def _validate_ini(self, content: str, file_path: str, start: float) -> SyntaxCheckResult:
        errors: list[ValidationIssue] = []
        parser = configparser.ConfigParser()
        try:
            parser.read_string(content)
        except configparser.Error as error:
            errors.append(
                ValidationIssue(
                    file_path=file_path,
                    message=str(error),
                    check="config-syntax",
                )
            )
        return self._result("syntax", [file_path], errors, [], start)

    def _validate_env(self, content: str, file_path: str, start: float) -> SyntaxCheckResult:
        errors: list[ValidationIssue] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                errors.append(
                    ValidationIssue(
                        file_path=file_path,
                        message="Environment-style config line must contain '='.",
                        line=line_number,
                        check="env-syntax",
                    )
                )
        return self._result("syntax", [file_path], errors, [], start)

    def _read_file(self, file_path: str, project_path: str | None) -> str | None:
        path = Path(file_path)
        if project_path and not path.is_absolute():
            path = Path(project_path) / path
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            log.exception("Could not read file for syntax validation path=%s", file_path)
            return None

    def _result(
        self,
        name: str,
        checked_files: list[str],
        errors: list[ValidationIssue],
        warnings: list[ValidationIssue],
        start: float,
    ) -> SyntaxCheckResult:
        status = result_status(errors, warnings)
        summary = self._summary(status, checked_files, errors, warnings)
        return SyntaxCheckResult(
            name=name,
            status=status,
            execution_time_seconds=round(time.monotonic() - start, 4),
            errors=errors,
            warnings=warnings,
            summary=summary,
            confidence_score=confidence_from_issues(errors, warnings),
            checked_files=checked_files,
        )

    def _summary(
        self,
        status: str,
        checked_files: list[str],
        errors: list[ValidationIssue],
        warnings: list[ValidationIssue],
    ) -> str:
        count = len(checked_files)
        if status == "pass":
            return f"Syntax valid for {count} file(s)."
        if errors:
            return f"Syntax validation found {len(errors)} error(s) across {count} file(s)."
        return f"Syntax validation completed with {len(warnings)} warning(s)."
