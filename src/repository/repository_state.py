"""Serializable repository state used as the repository-aware source of truth."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for repository state records."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _string_list(value: Any) -> list[str]:
    """Safely coerce serialized list-ish values into sorted unique strings."""
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if item is not None})


@dataclass
class RepositoryHealth:
    """Health signals derived from repository and index state."""

    is_git_repo: bool = False
    has_uncommitted_changes: bool = False
    index_outdated: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe health dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "RepositoryHealth":
        """Build health data from a tolerant serialized dictionary."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            is_git_repo=bool(data.get("is_git_repo", False)),
            has_uncommitted_changes=bool(data.get("has_uncommitted_changes", False)),
            index_outdated=bool(data.get("index_outdated", False)),
            warnings=_string_list(data.get("warnings", [])),
        )


@dataclass
class RepositoryState:
    """Central repository state shared by indexing, retrieval, and debugging modules."""

    repo_path: str
    branch: str = ""
    head_commit: str = ""
    indexed_at: str = ""
    last_scan: str = ""
    file_count: int = 0
    python_files: int = 0
    total_size_bytes: int = 0
    working_tree_fingerprint: str = ""
    changed_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    health: RepositoryHealth = field(default_factory=RepositoryHealth)

    def get_repository_summary(self) -> str:
        """Return a compact human-readable repository state summary."""
        return "\n".join(
            [
                "Repository Path:",
                self.repo_path or "Unknown",
                "",
                "Current Branch:",
                self.branch or "Unknown",
                "",
                "HEAD Commit:",
                self.head_commit or "Unavailable",
                "",
                "Last Scan:",
                self.last_scan or "Never",
                "",
                "Files Indexed:",
                str(self.file_count),
                "",
                "Python Files:",
                str(self.python_files),
                "",
                "Changed Files:",
                self._format_file_list(self.changed_files),
                "",
                "Staged Files:",
                self._format_file_list(self.staged_files),
                "",
                "Untracked Files:",
                self._format_file_list(self.untracked_files),
                "",
                "Repository Health:",
                self._format_health(),
            ]
        )

    def get_branch(self) -> str:
        """Return the current repository branch, if known."""
        return self.branch

    def get_head_commit(self) -> str:
        """Return the current HEAD commit, if known."""
        return self.head_commit

    def get_file_count(self) -> int:
        """Return the number of files included in the repository index."""
        return self.file_count

    @property
    def has_uncommitted_changes(self) -> bool:
        """Return True when tracked or untracked working tree changes exist."""
        return bool(self.changed_files or self.staged_files or self.untracked_files)

    def is_stale(
        self,
        current_branch: str | None = None,
        current_head_commit: str | None = None,
        changed_files: list[str] | None = None,
        staged_files: list[str] | None = None,
        untracked_files: list[str] | None = None,
        working_tree_fingerprint: str | None = None,
    ) -> bool:
        """Return True when supplied repository signals differ from this state."""
        if current_branch is not None and current_branch != self.branch:
            return True
        if current_head_commit is not None and current_head_commit != self.head_commit:
            return True
        if changed_files is not None and sorted(changed_files) != sorted(self.changed_files):
            return True
        if staged_files is not None and sorted(staged_files) != sorted(self.staged_files):
            return True
        if untracked_files is not None and sorted(untracked_files) != sorted(self.untracked_files):
            return True
        if working_tree_fingerprint is not None and working_tree_fingerprint != self.working_tree_fingerprint:
            return True
        return self.health.index_outdated

    def mark_indexed(self, indexed_at: str | None = None) -> None:
        """Mark the repository index as current for this state snapshot."""
        self.indexed_at = indexed_at or utc_now_iso()
        self.health.index_outdated = False

    def as_summary_dict(self) -> dict[str, Any]:
        """Return a structured summary for debugging, retrieval, and planning modules."""
        return {
            "metadata": {
                "repo_path": self.repo_path,
                "branch": self.branch,
                "head_commit": self.head_commit,
                "indexed_at": self.indexed_at,
                "last_scan": self.last_scan,
            },
            "statistics": {
                "file_count": self.file_count,
                "python_files": self.python_files,
                "total_size_bytes": self.total_size_bytes,
                "working_tree_fingerprint": self.working_tree_fingerprint,
            },
            "git": {
                "changed_files": list(self.changed_files),
                "staged_files": list(self.staged_files),
                "untracked_files": list(self.untracked_files),
            },
            "health": self.health.as_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation of this state."""
        data = asdict(self)
        data["changed_files"] = _string_list(data.get("changed_files"))
        data["staged_files"] = _string_list(data.get("staged_files"))
        data["untracked_files"] = _string_list(data.get("untracked_files"))
        data["health"] = self.health.as_dict()
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "RepositoryState":
        """Build state from a tolerant serialized dictionary."""
        if not isinstance(data, dict):
            raise ValueError("Repository state payload must be a dictionary.")

        return cls(
            repo_path=str(data.get("repo_path", "")),
            branch=str(data.get("branch", "")),
            head_commit=str(data.get("head_commit", "")),
            indexed_at=str(data.get("indexed_at", "")),
            last_scan=str(data.get("last_scan", "")),
            file_count=int(data.get("file_count") or 0),
            python_files=int(data.get("python_files") or 0),
            total_size_bytes=int(data.get("total_size_bytes") or 0),
            working_tree_fingerprint=str(data.get("working_tree_fingerprint", "")),
            changed_files=_string_list(data.get("changed_files", [])),
            staged_files=_string_list(data.get("staged_files", [])),
            untracked_files=_string_list(data.get("untracked_files", [])),
            health=RepositoryHealth.from_dict(data.get("health", {})),
        )

    def _format_file_list(self, files: list[str]) -> str:
        """Return a readable list value for summaries."""
        return "\n".join(files) if files else "None"

    def _format_health(self) -> str:
        """Return concise health text for human summaries."""
        values = [
            f"git repository: {'yes' if self.health.is_git_repo else 'no'}",
            f"uncommitted changes: {'yes' if self.has_uncommitted_changes else 'no'}",
            f"index outdated: {'yes' if self.health.index_outdated else 'no'}",
        ]
        if self.health.warnings:
            values.append("warnings: " + "; ".join(self.health.warnings))
        return "\n".join(values)
