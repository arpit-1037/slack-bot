"""Tool wrapper for syntax validation."""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

from src.repository.repository_indexer import RepositoryIndexer
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.validation.syntax_validator import SyntaxValidator
from src.validation.validation_models import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARNING,
    SyntaxCheckResult,
    ValidationIssue,
)


class SyntaxCheckTool(BaseTool):
    """Validate source and configuration syntax without modifying files."""

    metadata = ToolMetadata(
        name="validation.syntax_check",
        description="Validate syntax for one file, supplied content, selected files, or the repository.",
        category="validation",
        input_schema={
            "project_path": "Optional repository path.",
            "file_path": "Optional file to validate.",
            "file_paths": "Optional list of files to validate.",
            "content": "Optional in-memory content for file_path.",
        },
        output_schema={"result": "Structured syntax validation result."},
        tags=["validation", "syntax", "read-only"],
        read_only=True,
    )

    def __init__(
        self,
        syntax_validator: SyntaxValidator | None = None,
        indexer: RepositoryIndexer | None = None,
    ) -> None:
        self.syntax_validator = syntax_validator or SyntaxValidator()
        self.indexer = indexer or RepositoryIndexer()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        if tool_input.get("file_path") is not None and not isinstance(tool_input["file_path"], str):
            errors.append(ToolValidationError("file_path", "file_path must be a string."))
        if tool_input.get("content") is not None and not isinstance(tool_input["content"], str):
            errors.append(ToolValidationError("content", "content must be a string."))
        file_paths = tool_input.get("file_paths")
        if file_paths is not None and not _string_list(file_paths):
            errors.append(ToolValidationError("file_paths", "file_paths must be a list of strings."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        project_path = _project_path(tool_input)
        content = tool_input.get("content")
        file_path = str(tool_input.get("file_path") or "").strip()
        file_paths = list(tool_input.get("file_paths") or [])

        if content is not None:
            result = self.syntax_validator.validate_file(file_path or "<memory>", str(content), project_path)
        elif file_path:
            result = self.syntax_validator.validate_file(file_path, project_path=project_path)
        elif file_paths:
            result = self._validate_file_paths(file_paths, project_path)
        else:
            index = self.indexer.ensure_index(project_path)
            result = self.syntax_validator.validate_files(
                {
                    path: entry.get("content", "")
                    for path, entry in index.items()
                }
            )

        return self._success(
            {
                "project_path": project_path,
                "passed": result.ok,
                "result": result,
            }
        )

    def _validate_file_paths(self, file_paths: list[str], project_path: str) -> SyntaxCheckResult:
        start = time.monotonic()
        results = [
            self.syntax_validator.validate_file(path, project_path=project_path)
            for path in sorted(set(file_paths))
        ]
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        checked_files: list[str] = []
        for result in results:
            errors.extend(result.errors)
            warnings.extend(result.warnings)
            checked_files.extend(result.checked_files)

        status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
        return SyntaxCheckResult(
            name="syntax",
            status=status,
            execution_time_seconds=round(time.monotonic() - start, 4),
            errors=errors,
            warnings=warnings,
            checked_files=sorted(set(checked_files)),
            summary=self._summary(status, checked_files, errors, warnings),
            confidence_score=1.0 if status == STATUS_PASS else 0.4 if status == STATUS_FAIL else 0.7,
        )

    def _summary(
        self,
        status: str,
        checked_files: list[str],
        errors: list[ValidationIssue],
        warnings: list[ValidationIssue],
    ) -> str:
        if status == STATUS_PASS:
            return f"Syntax valid for {len(set(checked_files))} file(s)."
        if errors:
            return f"Syntax validation found {len(errors)} error(s)."
        return f"Syntax validation completed with {len(warnings)} warning(s)."


def _project_path(tool_input: Mapping[str, Any]) -> str:
    return os.path.abspath(
        os.path.expanduser(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or "."))
    )


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
