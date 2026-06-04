"""Tool for repository file metadata inspection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from src.repository.metadata_extractor import MetadataExtractor
from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class FileMetadataTool(BaseTool):
    """Return filesystem metadata for one repository file."""

    metadata = ToolMetadata(
        name="system.file_metadata",
        description="Inspect file size, extension, timestamps, and scanner support status.",
        category="system",
        input_schema={
            "project_path": "Optional repository path.",
            "path": "Repository-relative file path.",
        },
        output_schema={"metadata": "Structured file metadata."},
        tags=["system", "file", "metadata", "read-only"],
        read_only=True,
    )

    def __init__(self, metadata_extractor: MetadataExtractor | None = None) -> None:
        self.metadata_extractor = metadata_extractor or MetadataExtractor()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        path = tool_input.get("path")
        if not isinstance(path, str) or not path.strip():
            errors.append(ToolValidationError("path", "path is required and must be a string."))
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        root = _project_root(tool_input)
        path = _resolve_inside(root, str(tool_input["path"]))
        if not path.exists():
            return self._failure(f"File not found: {path}")
        if not path.is_file():
            return self._failure(f"Path is not a file: {path}")

        stat = path.stat()
        suffix = path.suffix
        line_count = self._line_count(path)
        return self._success(
            {
                "project_path": str(root),
                "path": str(path.relative_to(root)).replace(os.sep, "/"),
                "absolute_path": str(path),
                "name": path.name,
                "extension": suffix,
                "size_bytes": stat.st_size,
                "modified_time": stat.st_mtime,
                "created_time": stat.st_ctime,
                "line_count": line_count,
                "supported_by_repository_scanner": self.metadata_extractor.is_supported_file(path.name),
            }
        )

    def _line_count(self, path: Path) -> int | None:
        try:
            with path.open("rb") as file:
                return sum(1 for _ in file)
        except OSError:
            return None


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
