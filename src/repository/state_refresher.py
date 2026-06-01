"""Refresh and staleness detection for repository state."""

from __future__ import annotations

import os
import subprocess
import hashlib
from dataclasses import dataclass, field
from typing import Mapping

from src.repository.repository_scanner import RepositoryScanner, ScannedFile
from src.repository.repository_state import RepositoryHealth, RepositoryState, utc_now_iso
from src.repository.state_cache import RepositoryStateCache
from src.utils.helpers import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RepositoryChangeDetection:
    """Result of a cheap repository state comparison."""

    repository_changed: bool
    branch_changed: bool
    commit_changed: bool
    working_tree_changed: bool
    index_outdated: bool
    refresh_required: bool
    current_branch: str = ""
    current_head_commit: str = ""
    current_working_tree_fingerprint: str = ""
    changed_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_refresh(self) -> bool:
        """Return True when a refresh is required."""
        return self.refresh_required


class RepositoryStateRefresher:
    """Inspect git/filesystem state and keep a cached RepositoryState current."""

    def __init__(
        self,
        repo_path: str | None = None,
        scanner: RepositoryScanner | None = None,
        cache: RepositoryStateCache | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        self.repo_path = self._normalize_path(repo_path) if repo_path else None
        self.scanner = scanner or RepositoryScanner()
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds

    def refresh_state(
        self,
        project_path: str | None = None,
        force: bool = False,
        scanned_files: list[ScannedFile] | None = None,
        index: Mapping[str, Mapping] | None = None,
        mark_indexed: bool = True,
    ) -> RepositoryState:
        """Return current repository state, refreshing cache only when needed."""
        repo_path = self._resolve_repo_path(project_path)
        cache = self._cache_for(repo_path)
        cached_state = cache.load_state()

        if not force and scanned_files is None and index is None and cached_state is not None:
            detection = self.detect_changes(cached_state, repo_path)
            if not detection.refresh_required:
                log.info("Repository state cache is fresh repo=%s", repo_path)
                return cached_state

        git_state = self._inspect_git(repo_path)
        state = cached_state or RepositoryState(repo_path=repo_path)
        state.repo_path = repo_path
        state.branch = git_state["branch"]
        state.head_commit = git_state["head_commit"]
        state.changed_files = git_state["changed_files"]
        state.staged_files = git_state["staged_files"]
        state.untracked_files = git_state["untracked_files"]
        state.working_tree_fingerprint = git_state["working_tree_fingerprint"]
        state.health = RepositoryHealth(
            is_git_repo=git_state["is_git_repo"],
            has_uncommitted_changes=bool(
                git_state["changed_files"] or git_state["staged_files"] or git_state["untracked_files"]
            ),
            index_outdated=False,
            warnings=[] if git_state["is_git_repo"] else ["Repository path is not a git worktree."],
        )

        self.update_statistics(
            state=state,
            project_path=repo_path,
            scanned_files=scanned_files,
            index=index,
            mark_indexed=mark_indexed,
        )
        cache.save_state(state)
        log.info(
            "Refreshed repository state repo=%s branch=%s head=%s files=%d",
            repo_path,
            state.branch or "unknown",
            state.head_commit[:12] if state.head_commit else "unavailable",
            state.file_count,
        )
        return state

    def detect_changes(
        self,
        state: RepositoryState | None = None,
        project_path: str | None = None,
    ) -> RepositoryChangeDetection:
        """Detect whether cached repository state differs from current git metadata."""
        repo_path = self._resolve_repo_path(project_path or (state.repo_path if state else None))
        state = state or self._cache_for(repo_path).load_state()
        if state is None:
            return RepositoryChangeDetection(
                repository_changed=True,
                branch_changed=True,
                commit_changed=True,
                working_tree_changed=True,
                index_outdated=True,
                refresh_required=True,
                reasons=["no-cached-state"],
            )

        git_state = self._inspect_git(repo_path)
        branch_changed = state.branch != git_state["branch"]
        commit_changed = state.head_commit != git_state["head_commit"]
        changed_changed = sorted(state.changed_files) != git_state["changed_files"]
        staged_changed = sorted(state.staged_files) != git_state["staged_files"]
        untracked_changed = sorted(state.untracked_files) != git_state["untracked_files"]
        fingerprint_changed = state.working_tree_fingerprint != git_state["working_tree_fingerprint"]
        working_tree_changed = changed_changed or staged_changed or untracked_changed or fingerprint_changed
        cache_valid = self._cache_for(repo_path).is_cache_valid(state)
        index_outdated = bool(
            not cache_valid
            or not state.indexed_at
            or state.health.index_outdated
            or branch_changed
            or commit_changed
            or working_tree_changed
        )
        repository_changed = branch_changed or commit_changed or working_tree_changed
        reasons = self._change_reasons(
            cache_valid=cache_valid,
            state=state,
            branch_changed=branch_changed,
            commit_changed=commit_changed,
            changed_changed=changed_changed,
            staged_changed=staged_changed,
            untracked_changed=untracked_changed,
            fingerprint_changed=fingerprint_changed,
        )

        return RepositoryChangeDetection(
            repository_changed=repository_changed,
            branch_changed=branch_changed,
            commit_changed=commit_changed,
            working_tree_changed=working_tree_changed,
            index_outdated=index_outdated,
            refresh_required=index_outdated,
            current_branch=git_state["branch"],
            current_head_commit=git_state["head_commit"],
            current_working_tree_fingerprint=git_state["working_tree_fingerprint"],
            changed_files=git_state["changed_files"],
            staged_files=git_state["staged_files"],
            untracked_files=git_state["untracked_files"],
            reasons=reasons,
        )

    def update_statistics(
        self,
        state: RepositoryState,
        project_path: str | None = None,
        scanned_files: list[ScannedFile] | None = None,
        index: Mapping[str, Mapping] | None = None,
        mark_indexed: bool = True,
    ) -> RepositoryState:
        """Refresh file statistics from an index, scanner output, or a fresh scan."""
        repo_path = self._resolve_repo_path(project_path or state.repo_path)
        now = utc_now_iso()

        if index is not None:
            state.file_count = len(index)
            state.python_files = sum(1 for path, entry in index.items() if self._is_python_file(path, entry))
            state.total_size_bytes = sum(self._safe_int(entry.get("size", 0)) for entry in index.values())
        else:
            files = scanned_files if scanned_files is not None else self.scanner.scan(repo_path)
            state.file_count = len(files)
            state.python_files = sum(1 for file_info in files if self._is_python_file(file_info["path"], file_info))
            state.total_size_bytes = sum(self._safe_int(file_info.get("size", 0)) for file_info in files)

        state.last_scan = now
        if mark_indexed:
            state.mark_indexed(now)
        return state

    def is_stale(
        self,
        state: RepositoryState | None = None,
        project_path: str | None = None,
    ) -> bool:
        """Return True when the supplied or cached state no longer matches the repository."""
        return self.detect_changes(state=state, project_path=project_path).refresh_required

    def needs_refresh(
        self,
        state: RepositoryState | None = None,
        project_path: str | None = None,
    ) -> bool:
        """Return True when repository state should be refreshed before use."""
        return self.is_stale(state=state, project_path=project_path)

    def _inspect_git(self, repo_path: str) -> dict:
        """Collect current git metadata without mutating the repository."""
        is_git_repo = self._run_git(repo_path, ["rev-parse", "--is-inside-work-tree"]).lower() == "true"
        if not is_git_repo:
            return {
                "is_git_repo": False,
                "branch": "",
                "head_commit": "",
                "changed_files": [],
                "staged_files": [],
                "untracked_files": [],
                "working_tree_fingerprint": "",
            }

        branch = self._run_git(repo_path, ["branch", "--show-current"])
        if not branch:
            branch = self._run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch == "HEAD":
            branch = "DETACHED"

        changed_files = self._filter_internal_paths(repo_path, self._git_lines(repo_path, ["diff", "--name-only"]))
        staged_files = self._filter_internal_paths(repo_path, self._git_lines(repo_path, ["diff", "--cached", "--name-only"]))
        untracked_files = self._filter_internal_paths(
            repo_path,
            self._git_lines(repo_path, ["ls-files", "--others", "--exclude-standard"]),
        )
        working_tree_fingerprint = self._working_tree_fingerprint(
            repo_path,
            changed_files + staged_files + untracked_files,
        )

        return {
            "is_git_repo": True,
            "branch": branch,
            "head_commit": self._run_git(repo_path, ["rev-parse", "HEAD"]),
            "changed_files": changed_files,
            "staged_files": staged_files,
            "untracked_files": untracked_files,
            "working_tree_fingerprint": working_tree_fingerprint,
        }

    def _git_lines(self, repo_path: str, args: list[str]) -> list[str]:
        """Return sorted unique git command output lines."""
        return sorted({line.strip() for line in self._run_git(repo_path, args).splitlines() if line.strip()})

    def _run_git(self, repo_path: str, args: list[str]) -> str:
        """Run one read-only git command with safe defaults."""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
                env=env,
            )
        except Exception as error:
            log.debug("Git metadata command failed repo=%s args=%s error=%s", repo_path, args, error)
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _filter_internal_paths(self, repo_path: str, paths: list[str]) -> list[str]:
        """Remove state-manager cache files from repository change lists."""
        cache_path = self._cache_for(repo_path).cache_path
        try:
            relative_cache_path = os.path.relpath(cache_path, repo_path).replace(os.sep, "/")
        except ValueError:
            return paths
        if relative_cache_path.startswith("../"):
            return paths
        return [path for path in paths if path.replace(os.sep, "/") != relative_cache_path]

    def _working_tree_fingerprint(self, repo_path: str, paths: list[str]) -> str:
        """Hash current contents for paths that are outside the current commit."""
        digest = hashlib.sha256()
        for relative_path in sorted({path.replace(os.sep, "/") for path in paths}):
            absolute_path = os.path.join(repo_path, relative_path)
            digest.update(relative_path.encode("utf-8", errors="replace"))
            try:
                stat = os.stat(absolute_path)
            except OSError:
                digest.update(b":missing")
                continue
            digest.update(f":{stat.st_size}:".encode("ascii"))
            try:
                with open(absolute_path, "rb") as file:
                    for chunk in iter(lambda: file.read(65_536), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b":unreadable")
        return digest.hexdigest() if paths else ""

    def _cache_for(self, repo_path: str) -> RepositoryStateCache:
        """Return a cache bound to repo_path."""
        if self.cache is not None and self.cache.repo_path == repo_path:
            return self.cache
        self.cache = RepositoryStateCache(repo_path, ttl_seconds=self.cache_ttl_seconds)
        return self.cache

    def _resolve_repo_path(self, project_path: str | None) -> str:
        """Resolve a repository path from method input or constructor state."""
        if project_path:
            self.repo_path = self._normalize_path(project_path)
        if not self.repo_path:
            self.repo_path = self._normalize_path(".")
        return self.repo_path

    def _normalize_path(self, project_path: str) -> str:
        """Return an absolute expanded repository path."""
        return os.path.abspath(os.path.expanduser(project_path))

    def _is_python_file(self, path: str, entry: Mapping) -> bool:
        """Return True when an index/scanner entry represents a Python file."""
        return path.endswith(".py") or entry.get("extension") == ".py"

    def _safe_int(self, value: object) -> int:
        """Coerce a serialized numeric value safely."""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _change_reasons(
        self,
        cache_valid: bool,
        state: RepositoryState,
        branch_changed: bool,
        commit_changed: bool,
        changed_changed: bool,
        staged_changed: bool,
        untracked_changed: bool,
        fingerprint_changed: bool,
    ) -> list[str]:
        """Build stable reason labels for refresh decisions."""
        reasons = []
        if not cache_valid:
            reasons.append("cache-expired")
        if not state.indexed_at:
            reasons.append("index-missing")
        if state.health.index_outdated:
            reasons.append("index-marked-outdated")
        if branch_changed:
            reasons.append("branch-changed")
        if commit_changed:
            reasons.append("commit-changed")
        if changed_changed:
            reasons.append("changed-files-changed")
        if staged_changed:
            reasons.append("staged-files-changed")
        if untracked_changed:
            reasons.append("untracked-files-changed")
        if fingerprint_changed:
            reasons.append("working-tree-fingerprint-changed")
        return reasons


StateRefresher = RepositoryStateRefresher
