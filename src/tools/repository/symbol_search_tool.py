"""Tool for repository symbol search."""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.repository.repository_indexer import RepositoryIndexer
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class SymbolSearchTool(BaseTool):
    """Search indexed functions, methods, and classes."""

    metadata = ToolMetadata(
        name="repository.symbol_search",
        description="Search repository symbols by function, method, class, or any symbol type.",
        category="repository",
        input_schema={
            "project_path": "Optional repository path.",
            "query": "Required symbol name or partial name.",
            "kind": "Optional kind: any, function, method, class.",
            "exact": "When true, require an exact case-insensitive name match.",
            "max_results": "Maximum number of symbols to return.",
        },
        output_schema={"matches": "Matching symbols with path, kind, name, and line range."},
        tags=["repository", "symbols", "search", "read-only"],
        read_only=True,
    )

    VALID_KINDS = {"any", "function", "method", "class"}

    def __init__(self, indexer: RepositoryIndexer | None = None) -> None:
        self.indexer = indexer or RepositoryIndexer()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        query = tool_input.get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append(ToolValidationError("query", "query is required and must be a string."))
        kind = str(tool_input.get("kind") or "any")
        if kind not in self.VALID_KINDS:
            errors.append(ToolValidationError("kind", "kind must be one of: any, function, method, class."))
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
        kind = str(tool_input.get("kind") or "any")
        exact = bool(tool_input.get("exact", False))
        max_results = max(1, min(int(tool_input.get("max_results") or 20), 100))
        index = self.indexer.ensure_index(project_path)

        matches = []
        for path, entry in sorted(index.items()):
            symbols = entry["symbols"]
            if kind in {"any", "function"}:
                for function in symbols["functions"]:
                    if self._matches(function["name"], query, exact):
                        matches.append(self._symbol(path, "function", function))
            if kind in {"any", "class"}:
                for class_info in symbols["classes"]:
                    if self._matches(class_info["name"], query, exact):
                        matches.append(self._symbol(path, "class", class_info))
            if kind in {"any", "method"}:
                for class_info in symbols["classes"]:
                    for method in class_info.get("methods", []):
                        if self._matches(method["name"], query, exact):
                            item = self._symbol(path, "method", method)
                            item["class_name"] = class_info["name"]
                            matches.append(item)

        return self._success(
            {
                "project_path": project_path,
                "query": query,
                "kind": kind,
                "exact": exact,
                "matches": matches[:max_results],
                "total_matches": len(matches),
                "truncated": len(matches) > max_results,
            }
        )

    def _matches(self, name: str, query: str, exact: bool) -> bool:
        left = name.lower()
        right = query.lower()
        return left == right if exact else right in left

    def _symbol(self, path: str, kind: str, symbol: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "path": path,
            "kind": kind,
            "name": symbol.get("name", ""),
            "line_start": symbol.get("line_start"),
            "line_end": symbol.get("line_end"),
            "arguments": list(symbol.get("arguments", [])),
            "docstring": symbol.get("docstring"),
        }


def _project_path(tool_input: Mapping[str, Any]) -> str:
    return os.path.abspath(
        os.path.expanduser(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or "."))
    )
