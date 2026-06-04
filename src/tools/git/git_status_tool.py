"""Tool for read-only git status inspection."""

from __future__ import annotations

from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.tools.git_tool import GitTool


class GitStatusTool(BaseTool):
    """Return current branch and working-tree status."""

    metadata = ToolMetadata(
        name="git.status",
        description="Inspect current git status, staged files, unstaged files, and branch state.",
        category="git",
        input_schema={"repo_path": "Optional repository path."},
        output_schema={
            "branch": "Current branch name.",
            "staged_files": "Files staged for commit.",
            "unstaged_files": "Tracked files with unstaged changes.",
            "untracked_files": "Untracked files.",
        },
        tags=["git", "status", "read-only"],
        read_only=True,
    )

    def __init__(self, git_tool: GitTool | None = None) -> None:
        self.git_tool = git_tool or GitTool()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        repo_path = tool_input.get("repo_path")
        if repo_path is not None and not isinstance(repo_path, str):
            errors.append(ToolValidationError("repo_path", "repo_path must be a string."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        git_tool = self._git_tool(tool_input)
        if not git_tool.is_git_repo():
            return self._failure(
                f"Configured path is not a git repository: {git_tool.repo_path}"
            )

        status_short = git_tool.run_command(["status", "--short"])
        staged_files = _lines(git_tool.run_command(["diff", "--cached", "--name-only"]))
        unstaged_files = _lines(git_tool.run_command(["diff", "--name-only"]))
        untracked_files = _lines(
            git_tool.run_command(["ls-files", "--others", "--exclude-standard"])
        )

        return self._success(
            {
                "repo_path": git_tool.repo_path,
                "is_git_repo": True,
                "branch": (
                    git_tool.run_command(["branch", "--show-current"])
                    or git_tool.run_command(["rev-parse", "--abbrev-ref", "HEAD"])
                ),
                "head_commit": git_tool.run_command(["rev-parse", "--short", "HEAD"]),
                "status_short": status_short,
                "clean": not bool(status_short),
                "staged_files": staged_files,
                "unstaged_files": unstaged_files,
                "untracked_files": untracked_files,
                "branch_state": _lines(git_tool.run_command(["status", "--branch", "--short"])),
            }
        )

    def _git_tool(self, tool_input: Mapping[str, Any]) -> GitTool:
        repo_path = str(tool_input.get("repo_path") or "").strip()
        if repo_path:
            return GitTool(repo_path=repo_path)
        return self.git_tool


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]
