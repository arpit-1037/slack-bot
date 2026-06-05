"""Execution context for read-only repository investigations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExecutionContext:
    """Store repository, git, retrieval, tool output, and execution history."""

    execution_id: str
    project_path: str
    repository_context: dict[str, Any] = field(default_factory=dict)
    git_context: dict[str, Any] = field(default_factory=dict)
    retrieval_context: dict[str, Any] = field(default_factory=dict)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    execution_history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project_path = str(Path(self.project_path or ".").expanduser().resolve())

    def add_context(self, key: str, value: Any, category: str = "metadata") -> None:
        """Add a value to a named context category."""
        bucket = self._bucket(category)
        bucket[str(key)] = value

    def get_context(self, key: str, default: Any = None, category: str = "metadata") -> Any:
        """Return a value from a named context category."""
        return self._bucket(category).get(str(key), default)

    def record_tool_output(self, step_id: str, tool_name: str, output: dict[str, Any]) -> None:
        """Record one structured tool output in execution history."""
        item = {
            "step_id": step_id,
            "tool_name": tool_name,
            "output": output,
        }
        self.tool_outputs.append(item)
        self.execution_history.append(
            {
                "type": "tool",
                "step_id": step_id,
                "tool_name": tool_name,
                "success": bool(output.get("success")),
                "status": output.get("status", ""),
            }
        )

    def record_step(self, step_id: str, status: str, tool_count: int) -> None:
        """Record one completed execution step."""
        self.execution_history.append(
            {
                "type": "step",
                "step_id": step_id,
                "status": status,
                "tool_count": tool_count,
            }
        )

    def path_inside_project(self, path: str) -> bool:
        """Return True when a path resolves inside the execution project root."""
        root = Path(self.project_path).resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            return False
        return True

    def relative_path(self, path: str) -> str:
        """Return a stable project-relative path when possible."""
        root = Path(self.project_path).resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            return str(candidate.resolve().relative_to(root)).replace(os.sep, "/")
        except ValueError:
            return str(path)

    def _bucket(self, category: str) -> dict[str, Any]:
        if category == "repository":
            return self.repository_context
        if category == "git":
            return self.git_context
        if category == "retrieval":
            return self.retrieval_context
        return self.metadata
