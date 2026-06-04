"""Tool wrapper for safe lint execution."""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.validation.lint_runner import LintRunner


class LintTool(BaseTool):
    """Run repository lint checks through the existing lint runner."""

    metadata = ToolMetadata(
        name="validation.lint",
        description="Run a detected or requested linter against the repository or selected files.",
        category="validation",
        input_schema={
            "project_path": "Optional repository path.",
            "file_paths": "Optional repository-relative files to lint.",
            "linter": "Optional linter name.",
            "timeout_seconds": "Optional timeout override.",
        },
        output_schema={"result": "Structured lint execution result."},
        tags=["validation", "lint", "read-only"],
        read_only=True,
    )

    def __init__(self, lint_runner: LintRunner | None = None) -> None:
        self.lint_runner = lint_runner or LintRunner()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        file_paths = tool_input.get("file_paths")
        if file_paths is not None and not _string_list(file_paths):
            errors.append(ToolValidationError("file_paths", "file_paths must be a list of strings."))
        if tool_input.get("linter") is not None and not isinstance(tool_input["linter"], str):
            errors.append(ToolValidationError("linter", "linter must be a string."))
        if tool_input.get("timeout_seconds") is not None:
            try:
                int(tool_input["timeout_seconds"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("timeout_seconds", "timeout_seconds must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        project_path = _project_path(tool_input)
        timeout = tool_input.get("timeout_seconds")
        result = self.lint_runner.run_linting(
            project_path=project_path,
            file_paths=list(tool_input.get("file_paths") or []),
            linter=str(tool_input.get("linter") or "") or None,
            timeout_seconds=int(timeout) if timeout is not None else None,
        )
        return self._success(
            {
                "project_path": project_path,
                "passed": result.ok,
                "result": result,
            }
        )


def _project_path(tool_input: Mapping[str, Any]) -> str:
    return os.path.abspath(
        os.path.expanduser(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or "."))
    )


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
