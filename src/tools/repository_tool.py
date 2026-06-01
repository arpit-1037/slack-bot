"""Tool wrapper for repository intelligence and context selection."""

from __future__ import annotations

from src.repository.context_selector import ContextSelection, ContextSelector
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.repository.repository_scanner import RepositoryScanner
from src.repository.repository_state import RepositoryState


class RepositoryTool:
    """Read repository context through scanner or smart context selection."""

    def __init__(
        self,
        scanner: RepositoryScanner | None = None,
        indexer: RepositoryIndexer | None = None,
        context_selector: ContextSelector | None = None,
    ) -> None:
        self.scanner = scanner or RepositoryScanner()
        self.indexer = indexer or RepositoryIndexer(scanner=self.scanner)
        self.context_selector = context_selector or ContextSelector(indexer=self.indexer)
        self.indexer = self.context_selector.indexer

    def read_codebase(self, project_path: str) -> str:
        """Return supported files for legacy LLM context consumers."""
        index = self.indexer.ensure_index(project_path)
        repository_state = self.indexer.repository_state or self.indexer.get_repository_state(project_path)
        return self._format_index_context(index, repository_state)

    def select_context(
        self,
        project_path: str,
        task: str,
        request_id: str | None = None,
    ) -> ContextSelection:
        """Return task-targeted repository context."""
        return self.context_selector.select_context(
            project_path=project_path,
            task=task,
            request_id=request_id,
        )

    def _format_index_context(
        self,
        index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
    ) -> str:
        """Format indexed files for legacy prompt context consumers."""
        if not index:
            return "No supported repository files found."

        chunks = [
            "REPOSITORY STATE",
            repository_state.get_repository_summary(),
            "REPOSITORY FILES",
        ]
        for path, entry in sorted(index.items()):
            suffix = " (truncated)" if entry.get("truncated") else ""
            chunks.append(f"=== {path}{suffix} ===\n{entry.get('content', '')}")
        return "\n\n".join(chunks)
