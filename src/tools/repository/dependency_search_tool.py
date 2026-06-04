"""Tool for repository dependency lookups."""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import RepositoryIndexer
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class DependencySearchTool(BaseTool):
    """Inspect dependencies and dependents from the repository dependency mapper."""

    metadata = ToolMetadata(
        name="repository.dependency_search",
        description="Inspect files imported by a file and files that depend on it.",
        category="repository",
        input_schema={
            "project_path": "Optional repository path.",
            "file_path": "Optional exact repository file path.",
            "query": "Optional path search when file_path is not exact.",
            "max_results": "Maximum files to return.",
        },
        output_schema={"matches": "Files with dependency and dependent lists."},
        tags=["repository", "dependencies", "search", "read-only"],
        read_only=True,
    )

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        for field in ("project_path", "file_path", "query"):
            if tool_input.get(field) is not None and not isinstance(tool_input[field], str):
                errors.append(ToolValidationError(field, f"{field} must be a string."))
        if tool_input.get("max_results") is not None:
            try:
                int(tool_input["max_results"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("max_results", "max_results must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        project_path = _project_path(tool_input)
        max_results = max(1, min(int(tool_input.get("max_results") or 20), 100))
        index = self.indexer.ensure_index(project_path)
        state = self.indexer.get_repository_state(project_path)
        self.dependency_mapper.refresh(index, state)

        candidates = self._candidates(index, tool_input)
        matches = []
        for path in candidates[:max_results]:
            dependencies = self.dependency_mapper.get_dependencies(path)
            dependents = self.dependency_mapper.get_dependents(path)
            matches.append(
                {
                    "path": path,
                    "dependencies": dependencies,
                    "dependents": dependents,
                    "dependency_count": len(dependencies),
                    "dependent_count": len(dependents),
                }
            )

        return self._success(
            {
                "project_path": project_path,
                "file_path": str(tool_input.get("file_path") or "").strip(),
                "query": str(tool_input.get("query") or "").strip(),
                "matches": matches,
                "total_matches": len(candidates),
                "truncated": len(candidates) > max_results,
            }
        )

    def _candidates(
        self,
        index: Mapping[str, Any],
        tool_input: Mapping[str, Any],
    ) -> list[str]:
        file_path = str(tool_input.get("file_path") or "").strip().replace(os.sep, "/")
        if file_path and file_path in index:
            return [file_path]

        query = (file_path or str(tool_input.get("query") or "").strip()).lower()
        if query:
            return [
                path
                for path in sorted(index)
                if query in path.lower()
            ]

        return [
            path
            for path in sorted(index)
            if self.dependency_mapper.get_dependencies(path)
            or self.dependency_mapper.get_dependents(path)
        ]


def _project_path(tool_input: Mapping[str, Any]) -> str:
    return os.path.abspath(
        os.path.expanduser(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or "."))
    )
