"""Validation and staleness checks for repository memory."""

from __future__ import annotations

import os
from dataclasses import replace

from src.memory.memory_models import MemoryRelationship, RepositoryFact, RepositoryMemory
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.repository.repository_state import utc_now_iso
from src.utils.helpers import get_logger

log = get_logger(__name__)


class MemoryValidator:
    """Validate fact accuracy against current files, symbols, and relationships."""

    def __init__(self, indexer: RepositoryIndexer | None = None) -> None:
        self.indexer = indexer or RepositoryIndexer()

    def validate_memory(
        self,
        memory: RepositoryMemory,
        project_path: str | None = None,
    ) -> RepositoryMemory:
        """Return memory with stale or invalid entries marked invalid."""
        repo_path = os.path.abspath(os.path.expanduser(project_path or memory.repo_path))
        index = self.indexer.ensure_index(repo_path)
        state = self.indexer.repository_state or self.indexer.get_repository_state(repo_path)
        active_paths = set(state.changed_files + state.staged_files + state.untracked_files)
        facts = [self._validate_fact(fact, index, active_paths) for fact in memory.facts]
        relationships = [
            self._validate_relationship(relationship, index, active_paths)
            for relationship in memory.relationships
        ]
        validated = replace(
            memory,
            repo_path=repo_path,
            facts=facts,
            relationships=relationships,
            updated_at=utc_now_iso(),
            metadata={
                **memory.metadata,
                "validated_at": utc_now_iso(),
                "invalid_facts": sum(1 for fact in facts if not fact.valid),
                "invalid_relationships": sum(1 for relationship in relationships if not relationship.valid),
            },
        )
        log.info(
            "repository_memory validated repo=%s facts=%d invalid=%d relationships=%d invalid_relationships=%d",
            repo_path,
            len(facts),
            validated.metadata["invalid_facts"],
            len(relationships),
            validated.metadata["invalid_relationships"],
        )
        return validated

    def _validate_fact(
        self,
        fact: RepositoryFact,
        index: dict[str, FileIndexEntry],
        active_paths: set[str],
    ) -> RepositoryFact:
        if fact.file_path and fact.file_path not in index:
            return replace(
                fact,
                valid=False,
                stale_reason=f"file no longer exists: {fact.file_path}",
                confidence=min(fact.confidence, 0.2),
                updated_at=utc_now_iso(),
            )
        if fact.file_path in active_paths:
            return replace(
                fact,
                valid=False,
                stale_reason=f"file has uncommitted changes: {fact.file_path}",
                confidence=min(fact.confidence, 0.45),
                updated_at=utc_now_iso(),
            )
        if fact.symbol_name and fact.file_path:
            entry = index.get(fact.file_path)
            if entry is not None and not self._symbol_exists(entry, fact.symbol_name):
                return replace(
                    fact,
                    valid=False,
                    stale_reason=f"symbol no longer exists: {fact.symbol_name}",
                    confidence=min(fact.confidence, 0.3),
                    updated_at=utc_now_iso(),
                )
        return fact

    def _validate_relationship(
        self,
        relationship: MemoryRelationship,
        index: dict[str, FileIndexEntry],
        active_paths: set[str],
    ) -> MemoryRelationship:
        missing = []
        if relationship.source_path and relationship.source_path not in index:
            missing.append(relationship.source_path)
        if relationship.target_path and relationship.target_path not in index:
            missing.append(relationship.target_path)
        active = [path for path in (relationship.source_path, relationship.target_path) if path in active_paths]
        if missing or active:
            reason = "missing relationship files: " + ", ".join(missing) if missing else "relationship file has uncommitted changes: " + ", ".join(active)
            return replace(
                relationship,
                valid=False,
                stale_reason=reason,
                confidence=min(relationship.confidence, 0.35),
                updated_at=utc_now_iso(),
            )
        return relationship

    def _symbol_exists(self, entry: FileIndexEntry, symbol_name: str) -> bool:
        symbols = entry.get("symbols", {})
        needle = symbol_name.lower()
        for function in symbols.get("functions", []):
            if str(function.get("name", "")).lower() == needle:
                return True
        for class_info in symbols.get("classes", []):
            if str(class_info.get("name", "")).lower() == needle:
                return True
            for method in class_info.get("methods", []):
                if str(method.get("name", "")).lower() == needle:
                    return True
        return False


_default_validator: MemoryValidator | None = None


def default_memory_validator() -> MemoryValidator:
    """Return a lazily created memory validator."""
    global _default_validator
    if _default_validator is None:
        _default_validator = MemoryValidator()
    return _default_validator


def validate_memory(memory: RepositoryMemory, project_path: str | None = None) -> RepositoryMemory:
    """Validate repository memory using the default validator."""
    return default_memory_validator().validate_memory(memory, project_path=project_path)
