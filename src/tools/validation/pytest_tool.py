"""Tool wrapper for safe test execution."""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.validation.test_runner import TestRunner


class PytestTool(BaseTool):
    """Run repository tests through the existing safe test runner."""

    metadata = ToolMetadata(
        name="validation.pytest",
        description="Run pytest, unittest discovery, or an explicit configured test command.",
        category="validation",
        input_schema={
            "project_path": "Optional repository path.",
            "command": "Optional explicit test command string or argv list.",
            "timeout_seconds": "Optional timeout override.",
        },
        output_schema={"result": "Structured test execution result."},
        tags=["validation", "pytest", "tests", "read-only"],
        read_only=True,
    )

    def __init__(self, test_runner: TestRunner | None = None) -> None:
        self.test_runner = test_runner or TestRunner()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        command = tool_input.get("command")
        if command is not None and not isinstance(command, (str, list)):
            errors.append(ToolValidationError("command", "command must be a string or argv list."))
        if isinstance(command, list) and not all(isinstance(item, str) for item in command):
            errors.append(ToolValidationError("command", "command argv items must be strings."))
        if tool_input.get("timeout_seconds") is not None:
            try:
                int(tool_input["timeout_seconds"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("timeout_seconds", "timeout_seconds must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        project_path = _project_path(tool_input)
        timeout = tool_input.get("timeout_seconds")
        result = self.test_runner.run_tests(
            project_path=project_path,
            command=tool_input.get("command"),
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
