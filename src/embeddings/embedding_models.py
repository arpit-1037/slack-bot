"""Typed models for repository embeddings and semantic search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CodeChunk:
    """A semantic unit of repository code prepared for embedding."""

    chunk_id: str
    file_path: str
    content: str
    chunk_type: str
    symbol_name: str | None = None
    line_start: int = 1
    line_end: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text_for_embedding(self) -> str:
        """Return text that preserves code meaning plus light location hints."""
        symbol = f"{self.symbol_name}\n" if self.symbol_name else ""
        return f"{self.file_path}\n{self.chunk_type}\n{symbol}{self.content}".strip()


@dataclass(frozen=True)
class EmbeddingRecord:
    """Stored embedding vector for one code chunk."""

    chunk: CodeChunk
    embedding: list[float]
    model_name: str
    provider: str = "sentence-transformers"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    """Semantic search result returned from a vector store."""

    chunk: CodeChunk
    similarity_score: float
    distance: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingIndexMetadata:
    """Metadata for an embedding index snapshot."""

    project_path: str
    collection_name: str
    model_name: str
    chunk_count: int = 0
    file_count: int = 0
    indexed_at: str = ""
    repository_head: str | None = None
    changed_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingSearchResponse:
    """Semantic search response with query and ranked results."""

    query: str
    results: list[SearchResult]
    model_name: str
    collection_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
