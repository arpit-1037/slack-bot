"""Git command parsing, execution, and repository state helpers."""

from __future__ import annotations

import os
import re
import shlex
import subprocess

from src.utils.helpers import get_logger

log = get_logger(__name__)

QUOTE_CHARS = "'\"“”‘’"


def git_repo_path() -> str:
    """Return the configured repository path for git-aware operations."""
    path = os.getenv("GIT_REPO_PATH", ".").strip() or "."
    return os.path.abspath(os.path.expanduser(path))


def extract_git_commands(task: str) -> list[list[str]]:
    """Extract explicit `git ...` commands without invoking a shell."""
    commands = []
    for segment in re.split(r"(?:\n|&&|;)", task):
        cleaned = segment.strip().strip("`")
        if cleaned.startswith("$ "):
            cleaned = cleaned[2:].strip()
        git_index = cleaned.find("git ")
        if git_index == -1:
            continue
        cleaned = cleaned[git_index:]
        try:
            parts = shlex.split(cleaned)
        except ValueError:
            continue
        if len(parts) > 1 and parts[0] == "git":
            commands.append(parts[1:])
    return commands


def is_git_query(task: str) -> bool:
    """Return True when the task asks about repository state or history."""
    task_lower = task.lower()
    keywords = [
        "last commit", "changes", "diff", "what changed",
        "committed", "modified", "recent changes", "last committed",
        "what did i change", "show changes", "git", "branch",
        "merge", "stash", "rebase", "pull request", "commit history",
        "status", "staged", "unstaged", "untracked", "working tree",
        "head", "rollback", "revert", "reset", "latest commit",
    ]
    return any(keyword in task_lower for keyword in keywords)


def is_git_action_query(task: str) -> bool:
    """Return True when the task asks the bot to perform a git action."""
    task_lower = task.lower()
    if extract_git_commands(task):
        return True

    code_add_patterns = [
        r"\badd (?:support|tests?|a |an |feature|module|file|function|class)\b",
        r"\bcreate (?:tests?|a |an |module|file|function|class)\b",
        r"\bimplement\b",
    ]
    if (
        any(re.search(pattern, task_lower) for pattern in code_add_patterns)
        and "git" not in task_lower
        and "stage" not in task_lower
    ):
        return False

    action_patterns = [
        r"\bstage\b", r"\badd\b", r"\bcommit\b", r"\bpush\b",
        r"\bpull\b", r"\bfetch\b", r"\bstash\b", r"\bmerge\b",
        r"\brebase\b", r"\btag\b", r"\bcheckout\b", r"\bswitch\b",
        r"\brestore\b", r"\breset\b", r"\brevert\b", r"\bcherry[- ]pick\b",
    ]
    read_only_patterns = [
        r"\blast commit\b", r"\bcommit history\b", r"\bshow .*commit\b",
        r"\bwhat .*commit\b", r"\bwhat changed\b", r"\bdiff\b", r"\bstatus\b",
    ]
    return (
        any(re.search(pattern, task_lower) for pattern in action_patterns)
        and not any(re.search(pattern, task_lower) for pattern in read_only_patterns)
    )


class GitTool:
    """Encapsulate all git repository operations."""

    def __init__(self, repo_path: str | None = None) -> None:
        self._repo_path = os.path.abspath(
            os.path.expanduser(repo_path or os.getenv("GIT_REPO_PATH", ".").strip() or ".")
        )

    @property
    def repo_path(self) -> str:
        """Configured repository path."""
        return self._repo_path

    def run_command(self, args: list[str]) -> str:
        """Run a read-only git command and return stdout, preserving old fallback behavior."""
        try:
            log.debug("Running git command: %s", shlex.join(["git"] + args))
            return subprocess.check_output(
                ["git"] + args,
                stderr=subprocess.DEVNULL,
                cwd=self.repo_path,
            ).decode().strip()
        except Exception:
            return ""

    def run_action_command(self, args: list[str], timeout: int = 120) -> tuple[bool, str]:
        """Run a git command that may mutate repository state."""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            log.info("Running git action: %s", shlex.join(["git"] + args))
            result = subprocess.run(
                ["git"] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                env=env,
                cwd=self.repo_path,
            )
        except subprocess.TimeoutExpired:
            return False, "Command timed out."
        except Exception as error:
            return False, str(error)

        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        return result.returncode == 0, output or "Command completed."

    def has_git_changes(self, args: list[str]) -> bool:
        """Return True when a quiet git diff command reports changes."""
        try:
            result = subprocess.run(
                ["git"] + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.repo_path,
            )
        except Exception:
            return False
        return result.returncode == 1

    def has_staged_changes(self) -> bool:
        """Return True when staged changes exist."""
        return self.has_git_changes(["diff", "--cached", "--quiet"])

    def has_worktree_changes(self) -> bool:
        """Return True when unstaged or untracked changes exist."""
        if self.has_git_changes(["diff", "--quiet"]):
            return True
        return bool(self.run_command(["ls-files", "--others", "--exclude-standard"]))

    def format_git_result(self, args: list[str], ok: bool, output: str) -> str:
        """Format command output for Slack."""
        status = "OK" if ok else "FAILED"
        command = shlex.join(["git"] + args)
        return f"*{status}:* `{command}`\n```\n{output}\n```"

    def is_git_repo(self) -> bool:
        """Return True when the configured path is inside a git worktree."""
        result = self.run_command(["rev-parse", "--is-inside-work-tree"])
        return result.lower() == "true"

    def get_default_diff_range(self) -> tuple[str, str]:
        """Choose a safe recent diff range for repositories with one or more commits."""
        count = self.run_command(["rev-list", "--count", "HEAD"])
        try:
            if int(count) >= 2:
                return "HEAD~1", "HEAD"
        except Exception:
            pass
        return "", "HEAD"

    def get_raw_diff(self) -> str:
        """Return a raw repository diff summary without sending it through an LLM."""
        if not self.is_git_repo():
            return f"Could not fetch git diff: configured git project is not a repository: {self.repo_path}"

        try:
            commits = self.run_command(["log", "--oneline", "-3"]) or "No commits found."
            branch = self.run_command(["branch", "--show-current"]) or "Unknown branch"
            status = self.run_command(["status", "--short"]) or "Working tree clean"

            left, right = self.get_default_diff_range()

            if left:
                files = self.run_command(["diff", "--name-only", left, right]) or "No files changed."
                diff = self.run_command(["diff", left, right]) or "No diff found."
            else:
                files = self.run_command(["show", "--name-only", "--pretty=format:", right]) or "No files found."
                diff = self.run_command(["show", right, "--format="]) or "No diff found."

            return f"""*Current Branch:*
```
{branch}
```

*Last 3 Commits:*
```
{commits}
```

*Working Tree Status:*
```
{status}
```

*Files Changed:*
```
{files}
```

*Exact Diff:*
```diff
{diff}
```"""
        except Exception as error:
            return f"Could not fetch git diff: {error}"

    def extract_commit_message(self, task: str) -> str:
        """Extract a natural-language commit message from a Slack task."""
        patterns = [
            rf"(?:-m|--message)\s+[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
            rf"(?:commit message|message|msg)\s*[:=]\s*[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
            r"(?:commit message|message|msg)\s*[:=]\s*(.+)$",
            rf"\bwith\s+(?:commit\s+)?(?:message|msg)\s+[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
            rf"\bcommit\b.*\b(?:message|msg)\s+[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
        ]
        for pattern in patterns:
            match = re.search(pattern, task, re.IGNORECASE)
            if match:
                return match.group(1).strip().strip(": ")
        return ""

    def suggest_commit_message(self) -> str:
        """Suggest a commit message based on staged or unstaged filenames."""
        files = self.run_command(["diff", "--cached", "--name-only"]).splitlines()
        if not files:
            files = self.run_command(["diff", "--name-only"]).splitlines()

        if not files:
            return "Update project"
        if len(files) == 1:
            return f"Update {files[0]}"
        if len(files) <= 3:
            return "Update " + ", ".join(files)
        return f"Update {len(files)} files"

    def command_has_commit_message(self, args: list[str]) -> bool:
        """Return True when a git commit command already includes a message flag."""
        return any(
            arg == "-m"
            or arg == "--message"
            or arg.startswith("-m")
            or arg.startswith("--message=")
            or (arg.startswith("-") and not arg.startswith("--") and "m" in arg[1:])
            for arg in args
        )

    def normalize_git_command(self, args: list[str]) -> list[str]:
        """Normalize git command arguments before execution."""
        if args[:2] == ["commit", "command"]:
            args = ["commit"] + args[2:]
        if args and args[0] == "commit" and not self.command_has_commit_message(args):
            args = args + ["-m", self.suggest_commit_message()]
        return args

    def run_git_commands(self, commands: list[list[str]]) -> str:
        """Run one or more git commands and stop after the first failure."""
        if not self.is_git_repo():
            return f"Could not run git command: configured git project is not a repository: {self.repo_path}"
        if not commands:
            return "No git command found. Use an explicit command like `git status` or ask me to commit/push changes."

        results = []
        normalized_commands = []
        for args in commands:
            args = self.normalize_git_command(args)
            if args and args[0] == "commit" and not self.has_staged_changes() and self.has_worktree_changes():
                normalized_commands.append(["add", "-A"])
            normalized_commands.append(args)

        for args in normalized_commands:
            ok, output = self.run_action_command(args)
            results.append(self.format_git_result(args, ok, output))
            if not ok:
                break
        return "\n\n".join(results)

    def run_natural_git_action(self, task: str) -> str:
        """Translate a natural-language git action request into git commands."""
        task_lower = task.lower()
        commands = []

        wants_commit = bool(re.search(r"\bcommit\b", task_lower))
        wants_push = bool(re.search(r"\bpush\b", task_lower))
        wants_stage = bool(re.search(r"\b(stage|add)\b", task_lower))

        if re.search(r"\bpull\b", task_lower):
            commands.append(["pull"])
        if re.search(r"\bfetch\b", task_lower):
            commands.append(["fetch"])
        if re.search(r"\bstash\b", task_lower):
            commands.append(["stash", "push"])

        if wants_stage or (wants_commit and "staged" not in task_lower):
            commands.append(["add", "-A"])

        if wants_commit:
            message = self.extract_commit_message(task) or self.suggest_commit_message()
            commands.append(["commit", "-m", message])

        if wants_push:
            commands.append(["push"])

        return self.run_git_commands(commands)

    def run_git_action(self, task: str) -> str:
        """Run explicit git commands or a natural-language git action."""
        explicit_commands = extract_git_commands(task)
        if explicit_commands:
            return self.run_git_commands(explicit_commands)
        return self.run_natural_git_action(task)

    def get_git_context(self) -> str:
        """Build git repository context for LLM tasks that need project state."""
        if not self.is_git_repo():
            return f"GIT CONTEXT: Configured git project is not a repository: {self.repo_path}"

        context = []
        context.append(f"PROJECT PATH:\n{self.repo_path}")

        branch = self.run_command(["branch", "--show-current"]) or "Unknown branch"
        context.append(f"CURRENT BRANCH:\n{branch}")

        head_commit = self.run_command(["rev-parse", "--short", "HEAD"]) or "Unavailable"
        context.append(f"CURRENT HEAD:\n{head_commit}")

        recent_commits = self.run_command(["log", "--oneline", "--decorate", "-10"]) or "Git history unavailable."
        context.append(f"LAST 10 COMMITS:\n{recent_commits}")

        staged = self.run_command(["diff", "--cached", "--name-only"])
        context.append(f"STAGED FILES:\n{staged or 'None'}")

        unstaged = self.run_command(["diff", "--name-only"])
        context.append(f"UNSTAGED FILES:\n{unstaged or 'None'}")

        untracked = self.run_command(["ls-files", "--others", "--exclude-standard"])
        context.append(f"UNTRACKED FILES:\n{untracked or 'None'}")

        status = self.run_command(["status", "--short"])
        context.append(f"WORKING TREE STATUS:\n{status or 'Clean'}")

        left, right = self.get_default_diff_range()

        if left:
            changed_files = self.run_command(["diff", "--name-only", left, right])
            diff_stat = self.run_command(["diff", "--stat", left, right])
            diff = self.run_command(["diff", left, right])
        else:
            changed_files = self.run_command(["show", "--name-only", "--pretty=format:", right])
            diff_stat = self.run_command(["show", "--stat", "--oneline", right])
            diff = self.run_command(["show", right, "--format="])

        context.append(f"FILES CHANGED IN MOST RECENT COMPARISON:\n{changed_files or 'None'}")
        context.append(f"DIFF SUMMARY:\n{diff_stat or 'Unavailable.'}")
        context.append(f"RECENT DIFF:\n{diff or 'Unavailable.'}")

        return "\n\n".join(context)


_default_git_tool: GitTool | None = None


def default_git_tool() -> GitTool:
    """Return a lazily-created git tool using current environment configuration."""
    global _default_git_tool
    if _default_git_tool is None:
        _default_git_tool = GitTool()
    return _default_git_tool
