"""Tool for repository statistics."""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Mapping

from src.repository.repository_indexer import RepositoryIndexer
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class RepositoryStatsTool(BaseTool):
    """Return repository state and indexed file statistics."""

    metadata = ToolMetadata(
        name="repository.stats",
        description="Inspect repository file counts, size totals, extensions, and state metadata.",
        category="repository",
        input_schema={
            "project_path": "Optional repository path.",
            "largest_files_limit": "Maximum largest-file entries to return.",
        },
        output_schema={
            "state": "Central repository state summary.",
            "extensions": "File counts by extension.",
            "largest_files": "Largest indexed files.",
        },
        tags=["repository", "stats", "state", "read-only"],
        read_only=True,
    )

    def __init__(self, indexer: RepositoryIndexer | None = None) -> None:
        self.indexer = indexer or RepositoryIndexer()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        if tool_input.get("largest_files_limit") is not None:
            try:
                int(tool_input["largest_files_limit"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("largest_files_limit", "largest_files_limit must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        project_path = _project_path(tool_input)
        largest_limit = max(1, min(int(tool_input.get("largest_files_limit") or 10), 50))
        index = self.indexer.ensure_index(project_path)
        state = self.indexer.get_repository_state(project_path)

        extensions = Counter(entry.get("extension", "") or "<none>" for entry in index.values())
        largest_files = sorted(
            [
                {
                    "path": path,
                    "size": int(entry.get("size", 0)),
                    "extension": entry.get("extension", ""),
                    "truncated": bool(entry.get("truncated", False)),
                }
                for path, entry in index.items()
            ],
            key=lambda item: (-item["size"], item["path"]),
        )

        return self._success(
            {
                "project_path": project_path,
                "state": state.as_summary_dict(),
                "file_count": len(index),
                "extensions": dict(sorted(extensions.items())),
                "total_size_bytes": sum(int(entry.get("size", 0)) for entry in index.values()),
                "largest_files": largest_files[:largest_limit],
            }
        )


def _project_path(tool_input: Mapping[str, Any]) -> str:
    return os.path.abspath(
        os.path.expanduser(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or "."))
    )
