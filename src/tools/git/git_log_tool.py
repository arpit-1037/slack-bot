"""Tool for read-only git log inspection."""

from __future__ import annotations

from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.tools.git_tool import GitTool


class GitLogTool(BaseTool):
    """Return recent commit summaries with author metadata."""

    metadata = ToolMetadata(
        name="git.log",
        description="Inspect recent commits, summaries, dates, and author information.",
        category="git",
        input_schema={
            "repo_path": "Optional repository path.",
            "limit": "Optional number of commits, capped at 50.",
            "path": "Optional repository path filter.",
        },
        output_schema={"commits": "Recent commits with hash, author, date, and summary."},
        tags=["git", "history", "commits", "read-only"],
        read_only=True,
    )

    def __init__(self, git_tool: GitTool | None = None) -> None:
        self.git_tool = git_tool or GitTool()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        if tool_input.get("repo_path") is not None and not isinstance(tool_input["repo_path"], str):
            errors.append(ToolValidationError("repo_path", "repo_path must be a string."))
        if tool_input.get("path") is not None and not isinstance(tool_input["path"], str):
            errors.append(ToolValidationError("path", "path must be a string."))
        if tool_input.get("limit") is not None:
            try:
                int(tool_input["limit"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("limit", "limit must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        git_tool = self._git_tool(tool_input)
        if not git_tool.is_git_repo():
            return self._failure(
                f"Configured path is not a git repository: {git_tool.repo_path}"
            )

        limit = max(1, min(int(tool_input.get("limit") or 10), 50))
        command = [
            "log",
            f"-n{limit}",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s",
        ]
        path = str(tool_input.get("path") or "").strip()
        if path:
            command.extend(["--", path])

        commits = []
        for line in git_tool.run_command(command).splitlines():
            parts = line.split("\x1f")
            if len(parts) != 6:
                continue
            full_hash, short_hash, author_name, author_email, authored_at, summary = parts
            commits.append(
                {
                    "hash": full_hash,
                    "short_hash": short_hash,
                    "author_name": author_name,
                    "author_email": author_email,
                    "authored_at": authored_at,
                    "summary": summary,
                }
            )

        return self._success(
            {
                "repo_path": git_tool.repo_path,
                "limit": limit,
                "path": path,
                "commits": commits,
                "total_returned": len(commits),
            }
        )

    def _git_tool(self, tool_input: Mapping[str, Any]) -> GitTool:
        repo_path = str(tool_input.get("repo_path") or "").strip()
        if repo_path:
            return GitTool(repo_path=repo_path)
        return self.git_tool
