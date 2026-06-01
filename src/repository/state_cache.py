"""Repository state cache with safe JSON serialization and expiration."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from src.repository.repository_state import RepositoryState, utc_now_iso
from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)

CACHE_VERSION = 1
DEFAULT_CACHE_DIRNAME = ".repository_state"
DEFAULT_CACHE_FILENAME = "state.json"


class RepositoryStateCache:
    """Persist repository state between requests without rescanning the repository."""

    def __init__(
        self,
        repo_path: str,
        cache_path: str | Path | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self.repo_path = os.path.abspath(os.path.expanduser(repo_path))
        self.cache_path = Path(cache_path) if cache_path else self._default_cache_path()
        self.ttl_seconds = (
            ttl_seconds
            if ttl_seconds is not None
            else int_env("REPOSITORY_STATE_CACHE_TTL_SECONDS", 300, minimum=0)
        )

    def save_state(self, state: RepositoryState) -> bool:
        """Save state to disk using an atomic replace."""
        payload = {
            "version": CACHE_VERSION,
            "saved_at": utc_now_iso(),
            "state": state.to_dict(),
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as cache_file:
                json.dump(payload, cache_file, indent=2, sort_keys=True)
                cache_file.write("\n")
            os.replace(tmp_path, self.cache_path)
            log.info("Saved repository state cache path=%s repo=%s", self.cache_path, self.repo_path)
            return True
        except OSError as error:
            log.warning("Could not save repository state cache path=%s error=%s", self.cache_path, error)
            return False
        except TypeError as error:
            log.warning("Could not serialize repository state cache path=%s error=%s", self.cache_path, error)
            return False

    def load_state(self) -> RepositoryState | None:
        """Load a valid repository state from cache, returning None on any unsafe payload."""
        payload = self._read_payload()
        if not payload:
            return None

        try:
            if payload.get("version") != CACHE_VERSION:
                log.info("Ignoring repository state cache with unsupported version path=%s", self.cache_path)
                return None
            state = RepositoryState.from_dict(payload.get("state", {}))
        except (TypeError, ValueError) as error:
            log.warning("Ignoring invalid repository state cache path=%s error=%s", self.cache_path, error)
            return None

        if os.path.abspath(os.path.expanduser(state.repo_path)) != self.repo_path:
            log.info("Ignoring repository state cache for different repo path=%s", self.cache_path)
            return None
        return state

    def is_cache_valid(self, state: RepositoryState | None = None) -> bool:
        """Return True when the cache exists, matches this repo, and has not expired."""
        cached_state = state or self.load_state()
        if cached_state is None:
            return False
        if os.path.abspath(os.path.expanduser(cached_state.repo_path)) != self.repo_path:
            return False
        if not self.cache_path.exists():
            return False
        if self.ttl_seconds == 0:
            return True
        age_seconds = time.time() - self.cache_path.stat().st_mtime
        is_valid = age_seconds <= self.ttl_seconds
        if not is_valid:
            log.info(
                "Repository state cache expired path=%s age_seconds=%.2f ttl_seconds=%d",
                self.cache_path,
                age_seconds,
                self.ttl_seconds,
            )
        return is_valid

    def invalidate_cache(self) -> None:
        """Remove the repository state cache if it exists."""
        try:
            self.cache_path.unlink(missing_ok=True)
            log.info("Invalidated repository state cache path=%s", self.cache_path)
        except OSError as error:
            log.warning("Could not invalidate repository state cache path=%s error=%s", self.cache_path, error)

    def _default_cache_path(self) -> Path:
        """Resolve the default per-repository cache path."""
        configured_file = os.getenv("REPOSITORY_STATE_CACHE_PATH", "").strip()
        if configured_file:
            return Path(os.path.expanduser(configured_file))

        configured_dir = os.getenv("REPOSITORY_STATE_CACHE_DIR", "").strip()
        if configured_dir:
            return Path(os.path.expanduser(configured_dir)) / DEFAULT_CACHE_FILENAME

        git_dir = Path(self.repo_path) / ".git"
        if git_dir.is_dir():
            return git_dir / "slack-claude-bot" / DEFAULT_CACHE_FILENAME

        return Path(self.repo_path) / DEFAULT_CACHE_DIRNAME / DEFAULT_CACHE_FILENAME

    def _read_payload(self) -> dict[str, Any] | None:
        """Read the JSON cache payload with defensive error handling."""
        try:
            with self.cache_path.open("r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as error:
            log.warning("Could not parse repository state cache path=%s error=%s", self.cache_path, error)
            return None
        except OSError as error:
            log.warning("Could not read repository state cache path=%s error=%s", self.cache_path, error)
            return None

        if not isinstance(payload, dict):
            log.warning("Ignoring repository state cache with non-object payload path=%s", self.cache_path)
            return None
        return payload
