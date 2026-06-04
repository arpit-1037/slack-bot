"""Tool for read-only git branch inspection."""

from __future__ import annotations

from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.tools.git_tool import GitTool


class GitBranchTool(BaseTool):
    """Return current and available git branches."""

    metadata = ToolMetadata(
        name="git.branch",
        description="Inspect the current branch and available local or remote branches.",
        category="git",
        input_schema={
            "repo_path": "Optional repository path.",
            "include_remote": "When true, include remote branches.",
        },
        output_schema={
            "current_branch": "Current branch name.",
            "local_branches": "Local branch names.",
            "remote_branches": "Remote branch names when requested.",
        },
        tags=["git", "branch", "read-only"],
        read_only=True,
    )

    def __init__(self, git_tool: GitTool | None = None) -> None:
        self.git_tool = git_tool or GitTool()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        if tool_input.get("repo_path") is not None and not isinstance(tool_input["repo_path"], str):
            errors.append(ToolValidationError("repo_path", "repo_path must be a string."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        git_tool = self._git_tool(tool_input)
        if not git_tool.is_git_repo():
            return self._failure(
                f"Configured path is not a git repository: {git_tool.repo_path}"
            )

        include_remote = bool(tool_input.get("include_remote", False))
        current_branch = (
            git_tool.run_command(["branch", "--show-current"])
            or git_tool.run_command(["rev-parse", "--abbrev-ref", "HEAD"])
        )
        local_branches = _lines(git_tool.run_command(["branch", "--format=%(refname:short)"]))
        remote_branches = (
            _lines(git_tool.run_command(["branch", "-r", "--format=%(refname:short)"]))
            if include_remote
            else []
        )

        return self._success(
            {
                "repo_path": git_tool.repo_path,
                "current_branch": current_branch,
                "local_branches": local_branches,
                "remote_branches": remote_branches,
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
