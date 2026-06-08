"""Context store for controlled autonomous workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorkflowContext:
    """Store repository, git, retrieval, memory, and execution workflow context."""

    workflow_id: str
    task: str
    project_path: str
    repository_context: dict[str, Any] = field(default_factory=dict)
    git_context: dict[str, Any] = field(default_factory=dict)
    retrieval_context: dict[str, Any] = field(default_factory=dict)
    memory_context: dict[str, Any] = field(default_factory=dict)
    execution_history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project_path = str(Path(self.project_path or ".").expanduser().resolve())

    def add_context(self, key: str, value: Any, category: str = "metadata") -> None:
        """Add a value to a named workflow context category."""
        self._bucket(category)[str(key)] = value

    def get_context(self, key: str, default: Any = None, category: str = "metadata") -> Any:
        """Return a value from a named workflow context category."""
        return self._bucket(category).get(str(key), default)

    def record_execution(self, item: dict[str, Any]) -> None:
        """Record one workflow execution event."""
        self.execution_history.append(dict(item))

    def relative_path(self, path: str) -> str:
        """Return a repository-relative path when possible."""
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
        if category == "memory":
            return self.memory_context
        return self.metadata
