"""Vector storage abstraction for repository embeddings."""

from __future__ import annotations

import importlib
import math
from typing import Iterable

from src.embeddings.embedding_models import CodeChunk, EmbeddingRecord, SearchResult
from src.utils.helpers import get_logger

log = get_logger(__name__)


class VectorStore:
    """Store and search code embeddings with ChromaDB or an in-memory fallback."""

    def __init__(
        self,
        collection_name: str = "repository_code",
        persist_path: str | None = None,
        force_memory: bool = False,
    ) -> None:
        self.collection_name = collection_name
        self.persist_path = persist_path
        self.force_memory = force_memory
        self._records: dict[str, EmbeddingRecord] = {}
        self._collection = None
        self._backend = "memory"
        self._backend_checked = False

    @property
    def backend(self) -> str:
        """Return the active vector backend."""
        self._ensure_collection()
        return self._backend

    def add_embeddings(self, records: Iterable[EmbeddingRecord]) -> None:
        """Insert embedding records into the vector store."""
        record_list = list(records)
        if not record_list:
            return
        collection = self._ensure_collection()
        for record in record_list:
            self._records[record.chunk.chunk_id] = record

        if collection is not None:
            collection.add(
                ids=[record.chunk.chunk_id for record in record_list],
                embeddings=[record.embedding for record in record_list],
                documents=[record.chunk.content for record in record_list],
                metadatas=[self._metadata(record) for record in record_list],
            )
        log.info("Added embeddings backend=%s count=%d", self._backend, len(record_list))

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        file_paths: list[str] | None = None,
    ) -> list[SearchResult]:
        """Return nearest code chunks for a query embedding."""
        collection = self._ensure_collection()
        if collection is not None:
            return self._search_chroma(collection, query_embedding, limit, file_paths)
        return self._search_memory(query_embedding, limit, file_paths)

    def update_embeddings(self, records: Iterable[EmbeddingRecord]) -> None:
        """Replace existing records with new embeddings."""
        record_list = list(records)
        self.remove_embeddings([record.chunk.chunk_id for record in record_list])
        self.add_embeddings(record_list)

    def remove_embeddings(
        self,
        chunk_ids: Iterable[str] | None = None,
        file_paths: Iterable[str] | None = None,
    ) -> None:
        """Remove records by chunk id or repository file path."""
        ids = set(chunk_ids or [])
        paths = set(file_paths or [])
        if paths:
            ids.update(
                chunk_id
                for chunk_id, record in self._records.items()
                if record.chunk.file_path in paths
            )
        if not ids:
            return

        collection = self._ensure_collection()
        for chunk_id in ids:
            self._records.pop(chunk_id, None)
        if collection is not None:
            collection.delete(ids=sorted(ids))
        log.info("Removed embeddings backend=%s count=%d", self._backend, len(ids))

    def clear(self) -> None:
        """Remove all records from the current collection."""
        self.remove_embeddings(chunk_ids=list(self._records))

    def _ensure_collection(self):
        if self._collection is not None or self.force_memory or self._backend_checked:
            return self._collection
        self._backend_checked = True
        try:
            chromadb = importlib.import_module("chromadb")
            if self.persist_path:
                client = chromadb.PersistentClient(path=self.persist_path)
            else:
                client = chromadb.Client()
            self._collection = client.get_or_create_collection(name=self.collection_name)
            self._backend = "chromadb"
            log.info("Initialized ChromaDB vector store collection=%s", self.collection_name)
        except Exception as error:
            log.info("Could not initialize ChromaDB; using in-memory vector store: %s", error)
            self._collection = None
            self._backend = "memory"
        return self._collection

    def _search_chroma(
        self,
        collection,
        query_embedding: list[float],
        limit: int,
        file_paths: list[str] | None,
    ) -> list[SearchResult]:
        where = {"file_path": {"$in": file_paths}} if file_paths else None
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        results = []
        for chunk_id, distance, document, metadata in zip(ids, distances, documents, metadatas):
            record = self._records.get(chunk_id)
            chunk = record.chunk if record else self._chunk_from_metadata(chunk_id, document, metadata or {})
            similarity = max(0.0, min(1.0, 1.0 - float(distance)))
            results.append(
                SearchResult(
                    chunk=chunk,
                    similarity_score=round(similarity, 4),
                    distance=float(distance),
                    metadata=metadata or {},
                )
            )
        return results

    def _search_memory(
        self,
        query_embedding: list[float],
        limit: int,
        file_paths: list[str] | None,
    ) -> list[SearchResult]:
        path_filter = set(file_paths or [])
        results = []
        for record in self._records.values():
            if path_filter and record.chunk.file_path not in path_filter:
                continue
            similarity = self._cosine_similarity(query_embedding, record.embedding)
            results.append(
                SearchResult(
                    chunk=record.chunk,
                    similarity_score=round(similarity, 4),
                    distance=round(1.0 - similarity, 4),
                    metadata=record.metadata,
                )
            )
        results.sort(key=lambda item: (-item.similarity_score, item.chunk.file_path, item.chunk.line_start))
        return results[:limit]

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        dot = sum(left[index] * right[index] for index in range(size))
        left_norm = math.sqrt(sum(value * value for value in left[:size]))
        right_norm = math.sqrt(sum(value * value for value in right[:size]))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return max(0.0, min(1.0, dot / (left_norm * right_norm)))

    def _metadata(self, record: EmbeddingRecord) -> dict:
        chunk = record.chunk
        metadata = {
            "file_path": chunk.file_path,
            "symbol_name": chunk.symbol_name or "",
            "chunk_type": chunk.chunk_type,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
            "model_name": record.model_name,
            "provider": record.provider,
        }
        metadata.update({
            key: value
            for key, value in {**chunk.metadata, **record.metadata}.items()
            if isinstance(value, (str, int, float, bool))
        })
        return metadata

    def _chunk_from_metadata(self, chunk_id: str, document: str, metadata: dict) -> CodeChunk:
        return CodeChunk(
            chunk_id=chunk_id,
            file_path=str(metadata.get("file_path", "")),
            content=document or "",
            chunk_type=str(metadata.get("chunk_type", "unknown")),
            symbol_name=str(metadata.get("symbol_name") or "") or None,
            line_start=int(metadata.get("line_start") or 1),
            line_end=int(metadata.get("line_end") or 1),
            metadata=dict(metadata),
        )
