"""High-level repository memory facade."""

from __future__ import annotations

import os

from src.memory.memory_extractor import MemoryExtractor
from src.memory.memory_indexer import MemoryIndexer
from src.memory.memory_models import MemoryQuery, MemoryResult, RepositoryMemory as RepositoryMemoryData
from src.memory.memory_retriever import MemoryRetriever
from src.memory.memory_store import MemoryStore
from src.memory.memory_updater import MemoryUpdater
from src.memory.memory_validator import MemoryValidator


class RepositoryMemory:
    """Coordinate local repository memory extraction, retrieval, validation, and updates."""

    def __init__(
        self,
        project_path: str | None = None,
        store: MemoryStore | None = None,
        extractor: MemoryExtractor | None = None,
        retriever: MemoryRetriever | None = None,
        updater: MemoryUpdater | None = None,
        validator: MemoryValidator | None = None,
        indexer: MemoryIndexer | None = None,
    ) -> None:
        self.project_path = os.path.abspath(os.path.expanduser(project_path or "."))
        self.store = store or MemoryStore(self.project_path)
        self.extractor = extractor or MemoryExtractor()
        self.validator = validator or MemoryValidator(indexer=self.extractor.indexer)
        self.indexer = indexer or MemoryIndexer()
        self.retriever = retriever or MemoryRetriever(store=self.store, indexer=self.indexer)
        self.updater = updater or MemoryUpdater(
            store=self.store,
            extractor=self.extractor,
            validator=self.validator,
        )

    def load_memory(self) -> RepositoryMemoryData:
        """Load repository memory from local storage."""
        return self.store.load_memory()

    def save_memory(self, memory: RepositoryMemoryData) -> bool:
        """Save repository memory to local storage."""
        return self.store.save_memory(memory)

    def update_repository_memory(
        self,
        force: bool = False,
        request_id: str | None = None,
    ) -> RepositoryMemoryData:
        """Refresh stale repository memory."""
        return self.updater.update_repository_memory(
            self.project_path,
            force=force,
            request_id=request_id,
        )

    def retrieve_memory(
        self,
        query: str | MemoryQuery,
        min_confidence: float | None = None,
        max_results: int | None = None,
    ) -> MemoryResult:
        """Retrieve repository memory for a query."""
        return self.retriever.retrieve_memory(
            query,
            project_path=self.project_path,
            min_confidence=min_confidence,
            max_results=max_results,
        )

    def format_memory_result(self, result: MemoryResult) -> str:
        """Format a memory result for Slack."""
        return self.retriever.format_memory_result(result)

    def extract_repository_facts(self, request_id: str | None = None) -> RepositoryMemoryData:
        """Extract repository facts without persisting them."""
        return self.extractor.extract_repository_facts(self.project_path, request_id=request_id)

    def validate_memory(self, memory: RepositoryMemoryData | None = None) -> RepositoryMemoryData:
        """Validate repository memory against current files and symbols."""
        return self.validator.validate_memory(memory or self.load_memory(), project_path=self.project_path)

    def index_memory(self, memory: RepositoryMemoryData | None = None) -> dict:
        """Build searchable indexes for repository memory."""
        return self.indexer.index_memory(memory or self.load_memory())

    def build_relationship_graph(self, memory: RepositoryMemoryData | None = None) -> dict:
        """Build the repository memory relationship graph."""
        return self.indexer.build_relationship_graph(memory or self.load_memory())

    def store_execution_finding(self, summary: object) -> RepositoryMemoryData:
        """Persist repository facts from an execution summary."""
        return self.updater.store_execution_finding(summary, self.project_path)


_default_repository_memory: RepositoryMemory | None = None


def default_repository_memory(project_path: str | None = None) -> RepositoryMemory:
    """Return a lazily created repository memory facade."""
    global _default_repository_memory
    resolved = os.path.abspath(os.path.expanduser(project_path or "."))
    if _default_repository_memory is None or _default_repository_memory.project_path != resolved:
        _default_repository_memory = RepositoryMemory(resolved)
    return _default_repository_memory
