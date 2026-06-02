"""Embedding index construction and semantic search orchestration."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from src.embeddings.chunker import CodeChunker
from src.embeddings.embedding_models import EmbeddingIndexMetadata, EmbeddingRecord, EmbeddingSearchResponse, SearchResult
from src.embeddings.embedding_service import EmbeddingService
from src.embeddings.vector_store import VectorStore
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)


class EmbeddingIndexBuilder:
    """Build and maintain searchable repository embedding indexes."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        chunker: CodeChunker | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_store: VectorStore | None = None,
        collection_name: str | None = None,
        max_results: int | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.chunker = chunker or CodeChunker()
        self.embedding_service = embedding_service or EmbeddingService()
        self.collection_name = collection_name or "repository_code"
        self.vector_store = vector_store or VectorStore(collection_name=self.collection_name)
        self.max_results = max_results or int_env("EMBEDDING_SEARCH_LIMIT", 6, 1)
        self.metadata: EmbeddingIndexMetadata | None = None

    def build_index(self, project_path: str, request_id: str | None = None) -> EmbeddingIndexMetadata:
        """Build a full embedding index for a repository."""
        project_path = os.path.abspath(os.path.expanduser(project_path))
        repository_index = self.indexer.ensure_index(project_path)
        chunks = self.chunker.chunk_repository(repository_index)
        records = self._records_for_chunks(chunks)
        self.vector_store.clear()
        self.vector_store.add_embeddings(records)
        self.metadata = self._metadata(project_path, repository_index, len(chunks), request_id=request_id)
        log.info(
            "request_id=%s built embedding index files=%d chunks=%d backend=%s",
            request_id,
            len(repository_index),
            len(chunks),
            self.vector_store.backend,
        )
        return self.metadata

    def update_index(self, project_path: str, request_id: str | None = None) -> EmbeddingIndexMetadata:
        """Update the embedding index incrementally when possible."""
        if self.metadata is None:
            return self.build_index(project_path, request_id=request_id)
        return self.reindex_changed_files(project_path, request_id=request_id)

    def reindex_changed_files(
        self,
        project_path: str,
        changed_files: list[str] | None = None,
        request_id: str | None = None,
    ) -> EmbeddingIndexMetadata:
        """Re-index changed files and remove deleted indexed files."""
        project_path = os.path.abspath(os.path.expanduser(project_path))
        repository_index = self.indexer.ensure_index(project_path)
        repository_state = self.indexer.repository_state or self.indexer.get_repository_state(project_path)
        candidates = changed_files or sorted(
            set(repository_state.changed_files + repository_state.staged_files + repository_state.untracked_files)
        )
        candidates = [path for path in candidates if path in repository_index]

        if not candidates:
            self.metadata = self._metadata(project_path, repository_index, self._record_count(), request_id=request_id)
            return self.metadata

        self.vector_store.remove_embeddings(file_paths=candidates)
        chunks = [
            chunk
            for path in candidates
            for chunk in self.chunker.chunk_file(path, repository_index[path])
        ]
        self.vector_store.add_embeddings(self._records_for_chunks(chunks))
        self.metadata = self._metadata(
            project_path,
            repository_index,
            self._record_count(),
            changed_files=candidates,
            request_id=request_id,
        )
        log.info(
            "request_id=%s updated embedding index changed_files=%d chunks=%d",
            request_id,
            len(candidates),
            len(chunks),
        )
        return self.metadata

    def semantic_search(
        self,
        project_path: str,
        query: str,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> EmbeddingSearchResponse:
        """Search repository code by semantic similarity."""
        self.update_index(project_path, request_id=request_id)
        query_embedding = self.embedding_service.generate_embedding(query)
        results = self.vector_store.search(query_embedding, limit=limit or self.max_results)
        log.info(
            "request_id=%s semantic search results=%d query=%s",
            request_id,
            len(results),
            query[:120],
        )
        return EmbeddingSearchResponse(
            query=query,
            results=results,
            model_name=self.embedding_service.model_name,
            collection_name=self.collection_name,
            metadata={
                "provider": self.embedding_service.provider_name,
                "backend": self.vector_store.backend,
            },
        )

    def search_similar_code(
        self,
        project_path: str,
        code_or_query: str,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> list[SearchResult]:
        """Return semantic search results for code or natural-language text."""
        return self.semantic_search(
            project_path=project_path,
            query=code_or_query,
            limit=limit,
            request_id=request_id,
        ).results

    def _records_for_chunks(self, chunks) -> list[EmbeddingRecord]:
        texts = [chunk.text_for_embedding for chunk in chunks]
        embeddings = self.embedding_service.generate_embeddings(texts)
        return [
            EmbeddingRecord(
                chunk=chunk,
                embedding=embedding,
                model_name=self.embedding_service.model_name,
                provider=self.embedding_service.provider_name,
                metadata={
                    "embedding_sha256": hashlib.sha256(
                        ",".join(f"{value:.6f}" for value in embedding).encode("utf-8")
                    ).hexdigest(),
                },
            )
            for chunk, embedding in zip(chunks, embeddings)
        ]

    def _metadata(
        self,
        project_path: str,
        repository_index: dict[str, FileIndexEntry],
        chunk_count: int,
        changed_files: list[str] | None = None,
        request_id: str | None = None,
    ) -> EmbeddingIndexMetadata:
        repository_state = self.indexer.repository_state or self.indexer.get_repository_state(project_path)
        return EmbeddingIndexMetadata(
            project_path=project_path,
            collection_name=self.collection_name,
            model_name=self.embedding_service.model_name,
            chunk_count=chunk_count,
            file_count=len(repository_index),
            indexed_at=datetime.now(timezone.utc).isoformat(),
            repository_head=repository_state.head_commit if repository_state else None,
            changed_files=changed_files or [],
            metadata={
                "request_id": request_id,
                "provider": self.embedding_service.provider_name,
                "backend": self.vector_store.backend,
            },
        )

    def _record_count(self) -> int:
        return len(getattr(self.vector_store, "_records", {}))
