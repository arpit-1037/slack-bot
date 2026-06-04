"""Tool for repository file search."""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.repository.repository_indexer import RepositoryIndexer
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class FileSearchTool(BaseTool):
    """Search indexed repository file paths and optional file contents."""

    metadata = ToolMetadata(
        name="repository.file_search",
        description="Search repository files by path and optionally by content.",
        category="repository",
        input_schema={
            "project_path": "Optional repository path.",
            "query": "Required search query.",
            "search_content": "When true, search inside file contents.",
            "max_results": "Maximum number of matches to return.",
        },
        output_schema={"matches": "Matching files with path and optional content snippets."},
        tags=["repository", "files", "search", "read-only"],
        read_only=True,
    )

    def __init__(self, indexer: RepositoryIndexer | None = None) -> None:
        self.indexer = indexer or RepositoryIndexer()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        query = tool_input.get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append(ToolValidationError("query", "query is required and must be a string."))
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        if tool_input.get("max_results") is not None:
            try:
                int(tool_input["max_results"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("max_results", "max_results must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        project_path = _project_path(tool_input)
        query = str(tool_input["query"]).strip()
        needle = query.lower()
        search_content = bool(tool_input.get("search_content", False))
        max_results = max(1, min(int(tool_input.get("max_results") or 20), 100))

        index = self.indexer.ensure_index(project_path)
        matches = []
        for path, entry in sorted(index.items()):
            content = entry.get("content", "")
            path_match = needle in path.lower()
            content_matches = self._content_snippets(content, needle) if search_content else []
            if not path_match and not content_matches:
                continue
            matches.append(
                {
                    "path": path,
                    "extension": entry.get("extension", ""),
                    "size": entry.get("size", 0),
                    "truncated": bool(entry.get("truncated", False)),
                    "match_type": self._match_type(path_match, bool(content_matches)),
                    "content_snippets": content_matches,
                    "score": (2 if path_match else 0) + len(content_matches),
                }
            )

        matches.sort(key=lambda item: (-item["score"], item["path"]))
        return self._success(
            {
                "project_path": project_path,
                "query": query,
                "search_content": search_content,
                "matches": matches[:max_results],
                "total_matches": len(matches),
                "truncated": len(matches) > max_results,
            }
        )

    def _content_snippets(self, content: str, needle: str) -> list[dict[str, Any]]:
        snippets = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            if needle not in line.lower():
                continue
            snippets.append(
                {
                    "line": line_number,
                    "text": line.strip()[:240],
                }
            )
            if len(snippets) >= 3:
                break
        return snippets

    def _match_type(self, path_match: bool, content_match: bool) -> str:
        if path_match and content_match:
            return "path_and_content"
        if path_match:
            return "path"
        return "content"


def _project_path(tool_input: Mapping[str, Any]) -> str:
    return os.path.abspath(
        os.path.expanduser(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or "."))
    )
