"""Tool for read-only git diff inspection."""

from __future__ import annotations

from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.tools.git_tool import GitTool


class GitDiffTool(BaseTool):
    """Return file, working-tree, or commit diffs without mutating git state."""

    metadata = ToolMetadata(
        name="git.diff",
        description="Inspect file diffs, commit diffs, changed files, and diff stats.",
        category="git",
        input_schema={
            "repo_path": "Optional repository path.",
            "file_path": "Optional file path filter.",
            "base": "Optional base revision.",
            "target": "Optional target revision.",
            "commit": "Optional commit to inspect.",
            "cached": "When true, inspect staged changes.",
            "max_chars": "Maximum diff characters to return.",
        },
        output_schema={
            "changed_files": "Files changed by the requested diff.",
            "diff": "Unified diff text, truncated when needed.",
            "stat": "Diff stat text.",
        },
        tags=["git", "diff", "read-only"],
        read_only=True,
    )

    def __init__(self, git_tool: GitTool | None = None) -> None:
        self.git_tool = git_tool or GitTool()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        errors = []
        for field in ("repo_path", "file_path", "base", "target", "commit"):
            if tool_input.get(field) is not None and not isinstance(tool_input[field], str):
                errors.append(ToolValidationError(field, f"{field} must be a string."))
        if tool_input.get("max_chars") is not None:
            try:
                int(tool_input["max_chars"])
            except (TypeError, ValueError):
                errors.append(ToolValidationError("max_chars", "max_chars must be an integer."))
        return errors

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        git_tool = self._git_tool(tool_input)
        if not git_tool.is_git_repo():
            return self._failure(
                f"Configured path is not a git repository: {git_tool.repo_path}"
            )

        max_chars = max(1_000, min(int(tool_input.get("max_chars") or 20_000), 200_000))
        file_path = str(tool_input.get("file_path") or "").strip()
        commit = str(tool_input.get("commit") or "").strip()
        if commit:
            changed_files, stat, diff = self._commit_diff(git_tool, commit, file_path)
            mode = "commit"
        else:
            changed_files, stat, diff = self._range_or_worktree_diff(git_tool, tool_input, file_path)
            mode = "range" if tool_input.get("base") or tool_input.get("target") else "working_tree"

        truncated = len(diff) > max_chars
        if truncated:
            diff = diff[:max_chars]

        return self._success(
            {
                "repo_path": git_tool.repo_path,
                "mode": mode,
                "file_path": file_path,
                "changed_files": _lines(changed_files),
                "stat": stat,
                "diff": diff,
                "truncated": truncated,
                "max_chars": max_chars,
            }
        )

    def _range_or_worktree_diff(
        self,
        git_tool: GitTool,
        tool_input: Mapping[str, Any],
        file_path: str,
    ) -> tuple[str, str, str]:
        rev_args: list[str] = []
        if bool(tool_input.get("cached")):
            rev_args.append("--cached")
        base = str(tool_input.get("base") or "").strip()
        target = str(tool_input.get("target") or "").strip()
        if base and target:
            rev_args.extend([base, target])
        elif base:
            rev_args.append(base)

        path_args = ["--", file_path] if file_path else []
        changed_files = git_tool.run_command(["diff", "--name-only", *rev_args, *path_args])
        stat = git_tool.run_command(["diff", "--stat", *rev_args, *path_args])
        diff = git_tool.run_command(["diff", *rev_args, *path_args])
        return changed_files, stat, diff

    def _commit_diff(
        self,
        git_tool: GitTool,
        commit: str,
        file_path: str,
    ) -> tuple[str, str, str]:
        path_args = ["--", file_path] if file_path else []
        changed_files = git_tool.run_command(["show", "--name-only", "--pretty=format:", commit, *path_args])
        stat = git_tool.run_command(["show", "--stat", "--oneline", commit, *path_args])
        diff = git_tool.run_command(["show", "--format=", commit, *path_args])
        return changed_files, stat, diff

    def _git_tool(self, tool_input: Mapping[str, Any]) -> GitTool:
        repo_path = str(tool_input.get("repo_path") or "").strip()
        if repo_path:
            return GitTool(repo_path=repo_path)
        return self.git_tool


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]
