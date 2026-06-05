"""Refresh, invalidate, and extend repository memory."""

from __future__ import annotations

import os

from src.memory.memory_extractor import MemoryExtractor
from src.memory.memory_models import RepositoryFact, RepositoryMemory, stable_memory_id
from src.memory.memory_store import MemoryStore
from src.memory.memory_validator import MemoryValidator
from src.repository.repository_state import utc_now_iso
from src.repository.state_refresher import RepositoryStateRefresher
from src.utils.helpers import get_logger

log = get_logger(__name__)


class MemoryUpdater:
    """Update repository memory when repository state or execution findings change."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        extractor: MemoryExtractor | None = None,
        validator: MemoryValidator | None = None,
        state_refresher: RepositoryStateRefresher | None = None,
    ) -> None:
        self.store = store
        self.extractor = extractor or MemoryExtractor()
        self.validator = validator or MemoryValidator(indexer=self.extractor.indexer)
        self.state_refresher = state_refresher or RepositoryStateRefresher()

    def update_repository_memory(
        self,
        project_path: str,
        force: bool = False,
        request_id: str | None = None,
    ) -> RepositoryMemory:
        """Refresh stale facts and merge current repository knowledge."""
        repo_path = os.path.abspath(os.path.expanduser(project_path))
        store = self.store or MemoryStore(repo_path)
        memory = store.load_memory()
        detection = self.state_refresher.detect_changes(project_path=repo_path)
        should_extract = force or not memory.facts or detection.needs_refresh

        validated = self.validator.validate_memory(memory, project_path=repo_path) if memory.facts else memory
        if not should_extract:
            store.save_memory(validated)
            log.info("request_id=%s repository_memory update skipped fresh repo=%s", request_id, repo_path)
            return validated

        extracted = self.extractor.extract_repository_facts(repo_path, request_id=request_id)
        updated = store.update_memory(
            validated,
            facts=extracted.facts,
            relationships=extracted.relationships,
        )
        log.info(
            "request_id=%s repository_memory updated repo=%s reasons=%s facts=%d relationships=%d",
            request_id,
            repo_path,
            ",".join(detection.reasons) or "forced",
            len(updated.facts),
            len(updated.relationships),
        )
        return updated

    def store_execution_finding(
        self,
        summary: object,
        project_path: str,
    ) -> RepositoryMemory:
        """Store repository facts derived from execution findings without user conversation data."""
        repo_path = os.path.abspath(os.path.expanduser(project_path))
        store = self.store or MemoryStore(repo_path)
        memory = store.load_memory()
        facts = self._facts_from_execution_summary(summary, repo_path)
        if not facts:
            return memory
        updated = store.update_memory(memory, facts=facts)
        log.info("repository_memory stored execution findings repo=%s facts=%d", repo_path, len(facts))
        return updated

    def _facts_from_execution_summary(
        self,
        summary: object,
        repo_path: str,
    ) -> list[RepositoryFact]:
        files = [str(path) for path in getattr(summary, "files_examined", []) if path]
        issues = [str(issue) for issue in getattr(summary, "issues_found", []) if issue]
        facts: list[RepositoryFact] = []
        for path in files[:20]:
            facts.append(
                RepositoryFact(
                    id=stable_memory_id(repo_path, "execution_finding", "relevant file", path),
                    fact_type="execution_finding",
                    key="relevant repository file",
                    value=path,
                    confidence=0.78,
                    repo_path=repo_path,
                    file_path=path,
                    source="execution_engine",
                    evidence=[path],
                    tags=self._path_tags(path) + ["execution", "finding"],
                    metadata={"stored_at": utc_now_iso()},
                )
            )
        for issue in issues[:10]:
            facts.append(
                RepositoryFact(
                    id=stable_memory_id(repo_path, "execution_finding", "repository issue", issue),
                    fact_type="execution_finding",
                    key="repository issue",
                    value=issue[:500],
                    confidence=0.72,
                    repo_path=repo_path,
                    source="execution_engine",
                    evidence=[issue[:500]],
                    tags=["execution", "finding", "issue"],
                    metadata={"stored_at": utc_now_iso()},
                )
            )
        return facts

    def _path_tags(self, path: str) -> list[str]:
        return sorted({part for part in path.replace("\\", "/").replace(".", "/").split("/") if len(part) >= 3})


_default_updater: MemoryUpdater | None = None


def default_memory_updater() -> MemoryUpdater:
    """Return a lazily created memory updater."""
    global _default_updater
    if _default_updater is None:
        _default_updater = MemoryUpdater()
    return _default_updater


def update_repository_memory(
    project_path: str,
    force: bool = False,
    request_id: str | None = None,
) -> RepositoryMemory:
    """Update repository memory using the default updater."""
    return default_memory_updater().update_repository_memory(
        project_path,
        force=force,
        request_id=request_id,
    )


def store_execution_finding(summary: object, project_path: str) -> RepositoryMemory:
    """Store execution findings using the default updater."""
    return default_memory_updater().store_execution_finding(summary, project_path)
