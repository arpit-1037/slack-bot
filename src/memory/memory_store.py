"""Local JSON store for repository memory."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.memory.memory_models import (
    MemoryRelationship,
    RepositoryFact,
    RepositoryMemory,
    stable_memory_id,
)
from src.repository.repository_state import utc_now_iso
from src.utils.helpers import get_logger

log = get_logger(__name__)

MEMORY_STORE_VERSION = 1
DEFAULT_MEMORY_DIRNAME = ".repository_memory"
DEFAULT_MEMORY_FILENAME = "memory.json"


class MemoryStore:
    """Persist repository memory as local JSON with atomic updates."""

    def __init__(
        self,
        repo_path: str,
        storage_path: str | Path | None = None,
    ) -> None:
        self.repo_path = os.path.abspath(os.path.expanduser(repo_path))
        self.storage_path = Path(storage_path) if storage_path else self._default_storage_path()

    def load_memory(self) -> RepositoryMemory:
        """Load memory from disk, or return an empty repository memory."""
        payload = self._read_payload()
        if not payload:
            return RepositoryMemory.empty(self.repo_path)
        try:
            if payload.get("version") != MEMORY_STORE_VERSION:
                log.info("Ignoring repository memory with unsupported version path=%s", self.storage_path)
                return RepositoryMemory.empty(self.repo_path)
            memory = RepositoryMemory.from_dict(payload.get("memory", {}))
        except (TypeError, ValueError) as error:
            log.warning("Ignoring invalid repository memory path=%s error=%s", self.storage_path, error)
            return RepositoryMemory.empty(self.repo_path)

        if os.path.abspath(os.path.expanduser(memory.repo_path)) != self.repo_path:
            log.info("Ignoring repository memory for different repo path=%s", self.storage_path)
            return RepositoryMemory.empty(self.repo_path)
        return memory

    def save_memory(self, memory: RepositoryMemory) -> bool:
        """Save repository memory to disk using an atomic replace."""
        memory = replace(memory, updated_at=utc_now_iso())
        payload = {
            "version": MEMORY_STORE_VERSION,
            "saved_at": utc_now_iso(),
            "memory": memory.as_dict(),
        }
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as memory_file:
                json.dump(payload, memory_file, indent=2, sort_keys=True)
                memory_file.write("\n")
            os.replace(tmp_path, self.storage_path)
            log.info(
                "repository_memory saved path=%s repo=%s facts=%d relationships=%d",
                self.storage_path,
                self.repo_path,
                len(memory.facts),
                len(memory.relationships),
            )
            return True
        except OSError as error:
            log.warning("Could not save repository memory path=%s error=%s", self.storage_path, error)
            return False
        except TypeError as error:
            log.warning("Could not serialize repository memory path=%s error=%s", self.storage_path, error)
            return False

    def update_memory(
        self,
        memory: RepositoryMemory,
        facts: list[RepositoryFact] | None = None,
        relationships: list[MemoryRelationship] | None = None,
    ) -> RepositoryMemory:
        """Merge new facts and relationships into memory and persist it."""
        merged = RepositoryMemory(
            repo_path=memory.repo_path or self.repo_path,
            repo_id=memory.repo_id or stable_memory_id(self.repo_path),
            facts=self._merge_facts(memory.facts, facts or []),
            relationships=self._merge_relationships(memory.relationships, relationships or []),
            schema_version=memory.schema_version,
            updated_at=utc_now_iso(),
            metadata=dict(memory.metadata),
        )
        self.save_memory(merged)
        return merged

    def delete_memory(
        self,
        fact_ids: list[str] | None = None,
        relationship_ids: list[str] | None = None,
    ) -> RepositoryMemory:
        """Delete selected memory entries by id and persist the result."""
        memory = self.load_memory()
        fact_id_set = set(fact_ids or [])
        relationship_id_set = set(relationship_ids or [])
        updated = RepositoryMemory(
            repo_path=memory.repo_path,
            repo_id=memory.repo_id,
            facts=[fact for fact in memory.facts if fact.id not in fact_id_set],
            relationships=[
                relationship
                for relationship in memory.relationships
                if relationship.id not in relationship_id_set
            ],
            schema_version=memory.schema_version,
            updated_at=utc_now_iso(),
            metadata=dict(memory.metadata),
        )
        self.save_memory(updated)
        return updated

    def _merge_facts(
        self,
        current: list[RepositoryFact],
        incoming: list[RepositoryFact],
    ) -> list[RepositoryFact]:
        by_identity = {fact.identity_key: fact for fact in current}
        for fact in incoming:
            existing = by_identity.get(fact.identity_key)
            if existing is None:
                by_identity[fact.identity_key] = fact
                continue
            by_identity[fact.identity_key] = replace(
                existing if existing.confidence >= fact.confidence else fact,
                id=existing.id,
                created_at=existing.created_at,
                updated_at=utc_now_iso(),
                evidence=sorted(set(existing.evidence + fact.evidence)),
                tags=sorted(set(existing.tags + fact.tags)),
                valid=fact.valid,
                stale_reason=fact.stale_reason if not fact.valid else "",
            )
        return sorted(by_identity.values(), key=lambda item: (item.fact_type, item.key, item.value))

    def _merge_relationships(
        self,
        current: list[MemoryRelationship],
        incoming: list[MemoryRelationship],
    ) -> list[MemoryRelationship]:
        by_identity = {relationship.identity_key: relationship for relationship in current}
        for relationship in incoming:
            existing = by_identity.get(relationship.identity_key)
            if existing is None:
                by_identity[relationship.identity_key] = relationship
                continue
            by_identity[relationship.identity_key] = replace(
                existing if existing.confidence >= relationship.confidence else relationship,
                id=existing.id,
                created_at=existing.created_at,
                updated_at=utc_now_iso(),
                evidence=sorted(set(existing.evidence + relationship.evidence)),
                valid=relationship.valid,
                stale_reason=relationship.stale_reason if not relationship.valid else "",
            )
        return sorted(by_identity.values(), key=lambda item: (item.source, item.relationship_type, item.target))

    def _default_storage_path(self) -> Path:
        configured_file = os.getenv("REPOSITORY_MEMORY_PATH", "").strip()
        if configured_file:
            return Path(os.path.expanduser(configured_file))

        configured_dir = os.getenv("REPOSITORY_MEMORY_DIR", "").strip()
        if configured_dir:
            return Path(os.path.expanduser(configured_dir)) / DEFAULT_MEMORY_FILENAME

        git_dir = Path(self.repo_path) / ".git"
        if git_dir.is_dir():
            return git_dir / "slack-claude-bot" / "repository_memory.json"

        return Path(self.repo_path) / DEFAULT_MEMORY_DIRNAME / DEFAULT_MEMORY_FILENAME

    def _read_payload(self) -> dict[str, Any] | None:
        try:
            with self.storage_path.open("r", encoding="utf-8") as memory_file:
                payload = json.load(memory_file)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as error:
            log.warning("Could not parse repository memory path=%s error=%s", self.storage_path, error)
            return None
        except OSError as error:
            log.warning("Could not read repository memory path=%s error=%s", self.storage_path, error)
            return None
        if not isinstance(payload, dict):
            return None
        return payload


def save_memory(memory: RepositoryMemory, repo_path: str | None = None) -> bool:
    """Persist repository memory using the default store."""
    return MemoryStore(repo_path or memory.repo_path).save_memory(memory)


def load_memory(repo_path: str) -> RepositoryMemory:
    """Load repository memory using the default store."""
    return MemoryStore(repo_path).load_memory()


def update_memory(
    memory: RepositoryMemory,
    facts: list[RepositoryFact] | None = None,
    relationships: list[MemoryRelationship] | None = None,
) -> RepositoryMemory:
    """Update repository memory using the default store."""
    return MemoryStore(memory.repo_path).update_memory(memory, facts=facts, relationships=relationships)


def delete_memory(
    repo_path: str,
    fact_ids: list[str] | None = None,
    relationship_ids: list[str] | None = None,
) -> RepositoryMemory:
    """Delete selected repository memory entries using the default store."""
    return MemoryStore(repo_path).delete_memory(fact_ids=fact_ids, relationship_ids=relationship_ids)
