"""Tool for bounded repository file reads."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError


class FileReaderTool(BaseTool):
    """Read a repository file with path and size bounds."""

    metadata = ToolMetadata(
        name="system.file_reader",
        description="Read a repository file with optional byte and line limits.",
        category="system",
        input_schema={
            "project_path": "Optional repository path.",
            "path": "Repository-relative file path.",
            "max_bytes": "Maximum bytes to read.",
            "start_line": "Optional first line, 1-based.",
            "end_line": "Optional last line, inclusive.",
        },
        output_schema={"content": "Decoded file content and read metadata."},
        tags=["system", "file", "read-only"],
        read_only=True,
    )

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        path = tool_input.get("path")
        if not isinstance(path, str) or not path.strip():
            errors.append(ToolValidationError("path", "path is required and must be a string."))
        if tool_input.get("project_path") is not None and not isinstance(tool_input["project_path"], str):
            errors.append(ToolValidationError("project_path", "project_path must be a string."))
        for field in ("max_bytes", "start_line", "end_line"):
            if tool_input.get(field) is not None:
                try:
                    int(tool_input[field])
                except (TypeError, ValueError):
                    errors.append(ToolValidationError(field, f"{field} must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        root = _project_root(tool_input)
        path = _resolve_inside(root, str(tool_input["path"]))
        if not path.is_file():
            return self._failure(f"File not found: {path}")

        max_bytes = max(1, min(int(tool_input.get("max_bytes") or 200_000), 1_000_000))
        stat = path.stat()
        with path.open("rb") as file:
            raw = file.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        content = raw.decode("utf-8", errors="replace")
        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        start_line = tool_input.get("start_line")
        end_line = tool_input.get("end_line")
        selected_start = int(start_line) if start_line is not None else None
        selected_end = int(end_line) if end_line is not None else None
        if selected_start is not None or selected_end is not None:
            content, selected_start, selected_end = self._select_lines(
                content,
                selected_start,
                selected_end,
            )

        return self._success(
            {
                "project_path": str(root),
                "path": str(path.relative_to(root)).replace(os.sep, "/"),
                "absolute_path": str(path),
                "content": content,
                "encoding": "utf-8",
                "size_bytes": stat.st_size,
                "bytes_read": len(raw),
                "truncated": truncated,
                "line_count": total_lines,
                "start_line": selected_start,
                "end_line": selected_end,
            }
        )

    def _select_lines(
        self,
        content: str,
        start_line: int | None,
        end_line: int | None,
    ) -> tuple[str, int, int]:
        lines = content.splitlines(keepends=True)
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        if end < start:
            return "", start, end
        return "".join(lines[start - 1:end]), start, end


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
