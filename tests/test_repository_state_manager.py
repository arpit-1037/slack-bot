"""Testable examples for repository state refresh, cache, and staleness."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from src.repository.repository_indexer import RepositoryIndexer
from src.repository.repository_scanner import RepositoryScanner
from src.repository.repository_state import RepositoryState
from src.repository.state_cache import RepositoryStateCache
from src.repository.state_refresher import RepositoryStateRefresher


class RepositoryStateModelTest(unittest.TestCase):
    """Examples for repository state summaries and serialization."""

    def test_summary_and_round_trip(self) -> None:
        state = RepositoryState(
            repo_path="/repo",
            branch="main",
            head_commit="abc123",
            indexed_at="2026-01-01T00:00:00+00:00",
            last_scan="2026-01-01T00:00:00+00:00",
            file_count=3,
            python_files=2,
            changed_files=["src/app.py"],
        )

        loaded = RepositoryState.from_dict(state.to_dict())

        self.assertEqual(loaded.get_branch(), "main")
        self.assertEqual(loaded.get_head_commit(), "abc123")
        self.assertEqual(loaded.get_file_count(), 3)
        self.assertIn("Current Branch:", loaded.get_repository_summary())
        self.assertIn("src/app.py", loaded.get_repository_summary())


class RepositoryStateCacheTest(unittest.TestCase):
    """Examples for cache save, load, expiration, and invalidation."""

    def test_cache_validity_and_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = RepositoryStateCache(
                repo_path=str(root),
                cache_path=root / "cache" / "state.json",
                ttl_seconds=60,
            )
            state = RepositoryState(repo_path=str(root), branch="main")

            self.assertTrue(cache.save_state(state))
            self.assertTrue(cache.is_cache_valid())
            loaded = cache.load_state()
            self.assertIsNotNone(loaded)
            if loaded is not None:
                self.assertEqual(loaded.branch, "main")

            cache.invalidate_cache()
            self.assertFalse(cache.is_cache_valid())

    def test_cache_expiration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = RepositoryStateCache(
                repo_path=str(root),
                cache_path=root / "cache" / "state.json",
                ttl_seconds=1,
            )
            self.assertTrue(cache.save_state(RepositoryState(repo_path=str(root))))

            old_time = time.time() - 10
            os.utime(cache.cache_path, (old_time, old_time))

            self.assertFalse(cache.is_cache_valid())


class CountingScanner(RepositoryScanner):
    """Repository scanner that exposes scan count for freshness tests."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def scan(self, project_path: str):
        """Count and delegate repository scans."""
        self.calls += 1
        return super().scan(project_path)


class RepositoryStateRefresherTest(unittest.TestCase):
    """Examples for state refresh and staleness detection."""

    def test_indexer_reuses_fresh_state_without_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            scanner = CountingScanner()
            indexer = RepositoryIndexer(scanner=scanner)

            first_index = indexer.ensure_index(str(root))
            second_index = indexer.ensure_index(str(root))

            self.assertEqual(scanner.calls, 1)
            self.assertEqual(first_index, second_index)
            self.assertIsNotNone(indexer.repository_state)
            if indexer.repository_state is not None:
                self.assertEqual(indexer.repository_state.get_file_count(), 1)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_detects_git_worktree_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, ["init"])
            self._git(root, ["config", "user.email", "test@example.com"])
            self._git(root, ["config", "user.name", "Test User"])
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            self._git(root, ["add", "app.py"])
            self._git(root, ["commit", "-m", "initial"])

            refresher = RepositoryStateRefresher(repo_path=str(root))
            state = refresher.refresh_state(str(root), force=True)
            self.assertTrue(state.health.is_git_repo)
            self.assertEqual(state.python_files, 1)
            self.assertFalse(refresher.needs_refresh(state, str(root)))

            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            (root / "new_file.py").write_text("value = 3\n", encoding="utf-8")
            detection = refresher.detect_changes(state, str(root))

            self.assertTrue(detection.repository_changed)
            self.assertTrue(detection.needs_refresh)
            self.assertIn("app.py", detection.changed_files)
            self.assertIn("new_file.py", detection.untracked_files)

            refreshed = refresher.refresh_state(str(root), force=True)
            (root / "app.py").write_text("value = 4\n", encoding="utf-8")
            repeated_change = refresher.detect_changes(refreshed, str(root))

            self.assertTrue(repeated_change.needs_refresh)
            self.assertIn("working-tree-fingerprint-changed", repeated_change.reasons)

    def _git(self, cwd: Path, args: list[str]) -> None:
        subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
