"""Tool for bounded directory tree inspection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from src.repository.metadata_extractor import MetadataExtractor
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class DirectoryTreeTool(BaseTool):
    """Return a bounded directory tree for a repository path."""

    metadata = ToolMetadata(
        name="system.directory_tree",
        description="Inspect repository directories and files with depth and entry limits.",
        category="system",
        input_schema={
            "project_path": "Optional repository path.",
            "path": "Optional repository-relative directory path.",
            "max_depth": "Maximum directory depth.",
            "max_entries": "Maximum entries to return.",
            "include_files": "When false, return directories only.",
        },
        output_schema={"entries": "Flat directory tree entries with type and depth."},
        tags=["system", "directory", "tree", "read-only"],
        read_only=True,
    )

    def __init__(self, metadata_extractor: MetadataExtractor | None = None) -> None:
        self.metadata_extractor = metadata_extractor or MetadataExtractor()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        for field in ("project_path", "path"):
            if tool_input.get(field) is not None and not isinstance(tool_input[field], str):
                errors.append(ToolValidationError(field, f"{field} must be a string."))
        for field in ("max_depth", "max_entries"):
            if tool_input.get(field) is not None:
                try:
                    int(tool_input[field])
                except (TypeError, ValueError):
                    errors.append(ToolValidationError(field, f"{field} must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        root = _project_root(tool_input)
        start = _resolve_inside(root, str(tool_input.get("path") or "."))
        if not start.is_dir():
            return self._failure(f"Directory not found: {start}")

        max_depth = max(0, min(int(tool_input.get("max_depth") or 3), 12))
        max_entries = max(1, min(int(tool_input.get("max_entries") or 200), 2_000))
        include_files = bool(tool_input.get("include_files", True))
        entries = []

        for current_root, dirnames, filenames in os.walk(start):
            current = Path(current_root)
            relative_current = current.relative_to(start)
            depth = 0 if str(relative_current) == "." else len(relative_current.parts)
            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if not self.metadata_extractor.should_ignore_dir(dirname)
            ]
            if depth >= max_depth:
                dirnames[:] = []

            for dirname in dirnames:
                entries.append(self._entry(root, current / dirname, "directory", depth + 1))
                if len(entries) >= max_entries:
                    return self._result(root, start, entries, max_depth, max_entries, True)

            if include_files:
                for filename in sorted(filenames):
                    entries.append(self._entry(root, current / filename, "file", depth + 1))
                    if len(entries) >= max_entries:
                        return self._result(root, start, entries, max_depth, max_entries, True)

        return self._result(root, start, entries, max_depth, max_entries, False)

    def _entry(self, root: Path, path: Path, entry_type: str, depth: int) -> dict[str, Any]:
        return {
            "path": str(path.relative_to(root)).replace(os.sep, "/"),
            "name": path.name,
            "type": entry_type,
            "depth": depth,
        }

    def _result(
        self,
        root: Path,
        start: Path,
        entries: list[dict[str, Any]],
        max_depth: int,
        max_entries: int,
        truncated: bool,
    ) -> ToolResult:
        return self._success(
            {
                "project_path": str(root),
                "path": str(start.relative_to(root)).replace(os.sep, "/"),
                "entries": entries,
                "max_depth": max_depth,
                "max_entries": max_entries,
                "truncated": truncated,
            }
        )


def _project_root(tool_input: Mapping[str, Any]) -> Path:
    return Path(str(tool_input.get("project_path") or os.getenv("GIT_REPO_PATH", ".") or ".")).expanduser().resolve()


def _resolve_inside(root: Path, path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Path escapes project root: {path}") from error
    return resolved
