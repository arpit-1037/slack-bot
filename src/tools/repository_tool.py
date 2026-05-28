"""Tool wrapper for repository scanning."""

from __future__ import annotations

from src.repository.repository_scanner import RepositoryScanner


class RepositoryTool:
    """Read repository context through the scanner."""

    def __init__(self, scanner: RepositoryScanner | None = None) -> None:
        self.scanner = scanner or RepositoryScanner()

    def read_codebase(self, project_path: str) -> str:
        """Return supported source and configuration files for LLM context."""
        return self.scanner.scan(project_path)
