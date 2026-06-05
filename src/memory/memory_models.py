"""Typed models for repository-only memory."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.repository.repository_state import utc_now_iso

FactType = Literal[
    "architecture",
    "database",
    "entry_point",
    "execution_finding",
    "file",
    "framework",
    "module",
    "relationship",
    "symbol",
    "vector_store",
]


def stable_memory_id(*parts: str) -> str:
    """Return a deterministic short id for memory records."""
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class RepositoryFact:
    """One repository fact that can be reused across future requests."""

    id: str
    fact_type: FactType
    key: str
    value: str
    confidence: float
    repo_path: str = ""
    file_path: str = ""
    symbol_name: str = ""
    source: str = "repository_memory"
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    branch: str = ""
    head_commit: str = ""
    valid: bool = True
    stale_reason: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def identity_key(self) -> tuple[str, str, str, str, str]:
        """Return the logical identity used for deduplication."""
        return (
            self.fact_type,
            self.key.lower(),
            self.value.lower(),
            self.file_path,
            self.symbol_name.lower(),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "RepositoryFact":
        """Build a repository fact from tolerant serialized data."""
        if not isinstance(data, dict):
            raise ValueError("Repository fact payload must be a dictionary.")
        return cls(
            id=str(data.get("id") or stable_memory_id(data.get("fact_type", ""), data.get("key", ""), data.get("value", ""))),
            fact_type=str(data.get("fact_type") or "architecture"),  # type: ignore[arg-type]
            key=str(data.get("key") or ""),
            value=str(data.get("value") or ""),
            confidence=_bounded_float(data.get("confidence"), 0.0, 1.0),
            repo_path=str(data.get("repo_path") or ""),
            file_path=str(data.get("file_path") or ""),
            symbol_name=str(data.get("symbol_name") or ""),
            source=str(data.get("source") or "repository_memory"),
            evidence=_string_list(data.get("evidence", [])),
            tags=_string_list(data.get("tags", [])),
            branch=str(data.get("branch") or ""),
            head_commit=str(data.get("head_commit") or ""),
            valid=bool(data.get("valid", True)),
            stale_reason=str(data.get("stale_reason") or ""),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MemoryRelationship:
    """One relationship between repository entities."""

    id: str
    source: str
    target: str
    relationship_type: str
    confidence: float
    source_path: str = ""
    target_path: str = ""
    evidence: list[str] = field(default_factory=list)
    valid: bool = True
    stale_reason: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def identity_key(self) -> tuple[str, str, str]:
        """Return the logical identity used for deduplication."""
        return (self.source, self.relationship_type, self.target)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "MemoryRelationship":
        """Build a relationship from tolerant serialized data."""
        if not isinstance(data, dict):
            raise ValueError("Memory relationship payload must be a dictionary.")
        return cls(
            id=str(data.get("id") or stable_memory_id(data.get("source", ""), data.get("relationship_type", ""), data.get("target", ""))),
            source=str(data.get("source") or ""),
            target=str(data.get("target") or ""),
            relationship_type=str(data.get("relationship_type") or "uses"),
            confidence=_bounded_float(data.get("confidence"), 0.0, 1.0),
            source_path=str(data.get("source_path") or ""),
            target_path=str(data.get("target_path") or ""),
            evidence=_string_list(data.get("evidence", [])),
            valid=bool(data.get("valid", True)),
            stale_reason=str(data.get("stale_reason") or ""),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RepositoryMemory:
    """Repository-scoped memory containing facts and relationships only."""

    repo_path: str
    repo_id: str
    facts: list[RepositoryFact] = field(default_factory=list)
    relationships: list[MemoryRelationship] = field(default_factory=list)
    schema_version: int = 1
    updated_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def active_facts(self) -> list[RepositoryFact]:
        """Return valid facts only."""
        return [fact for fact in self.facts if fact.valid]

    @property
    def active_relationships(self) -> list[MemoryRelationship]:
        """Return valid relationships only."""
        return [relationship for relationship in self.relationships if relationship.valid]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "repo_path": self.repo_path,
            "repo_id": self.repo_id,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "facts": [fact.as_dict() for fact in self.facts],
            "relationships": [
                relationship.as_dict()
                for relationship in self.relationships
            ],
        }

    @classmethod
    def empty(cls, repo_path: str) -> "RepositoryMemory":
        """Return an empty memory object for a repository."""
        return cls(repo_path=repo_path, repo_id=stable_memory_id(repo_path))

    @classmethod
    def from_dict(cls, data: Any) -> "RepositoryMemory":
        """Build repository memory from tolerant serialized data."""
        if not isinstance(data, dict):
            raise ValueError("Repository memory payload must be a dictionary.")
        repo_path = str(data.get("repo_path") or "")
        return cls(
            repo_path=repo_path,
            repo_id=str(data.get("repo_id") or stable_memory_id(repo_path)),
            facts=[
                RepositoryFact.from_dict(item)
                for item in data.get("facts", [])
                if isinstance(item, dict)
            ],
            relationships=[
                MemoryRelationship.from_dict(item)
                for item in data.get("relationships", [])
                if isinstance(item, dict)
            ],
            schema_version=int(data.get("schema_version") or 1),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MemoryEntry:
    """One ranked memory result."""

    fact: RepositoryFact
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return {
            "fact": self.fact.as_dict(),
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class MemoryQuery:
    """A repository-memory lookup request."""

    query: str
    project_path: str = ""
    max_results: int = 5
    min_confidence: float = 0.85
    include_stale: bool = False


@dataclass(frozen=True)
class MemoryResult:
    """Repository-memory lookup result with hit/miss metadata."""

    query: MemoryQuery
    entries: list[MemoryEntry] = field(default_factory=list)
    hit: bool = False
    best_confidence: float = 0.0
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def facts(self) -> list[RepositoryFact]:
        """Return ranked facts."""
        return [entry.fact for entry in self.entries]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation."""
        return {
            "query": asdict(self.query),
            "entries": [entry.as_dict() for entry in self.entries],
            "hit": self.hit,
            "best_confidence": self.best_confidence,
            "summary": self.summary,
            "metadata": self.metadata,
        }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if item is not None})


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
