"""Tool wrapper for repository intelligence and context selection."""

from __future__ import annotations

from src.repository.context_selector import ContextSelection, ContextSelector
from src.repository.repository_scanner import RepositoryScanner


class RepositoryTool:
    """Read repository context through scanner or smart context selection."""

    def __init__(
        self,
        scanner: RepositoryScanner | None = None,
        context_selector: ContextSelector | None = None,
    ) -> None:
        self.scanner = scanner or RepositoryScanner()
        self.context_selector = context_selector or ContextSelector()

    def read_codebase(self, project_path: str) -> str:
        """Return supported files for legacy LLM context consumers."""
        return self.scanner.format_context(self.scanner.scan(project_path))

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
