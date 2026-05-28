"""Recursive repository scanner for LLM project context."""

from __future__ import annotations

import os

from src.repository.file_loader import FileLoader
from src.repository.metadata_extractor import MetadataExtractor
from src.utils.helpers import get_logger

log = get_logger(__name__)


class RepositoryScanner:
    """Scan supported repository files while excluding heavyweight directories."""

    def __init__(
        self,
        file_loader: FileLoader | None = None,
        metadata: MetadataExtractor | None = None,
    ) -> None:
        self.file_loader = file_loader or FileLoader()
        self.metadata = metadata or MetadataExtractor()

    def scan(self, project_path: str) -> str:
        """Return repository file contents in a stable, nested-path-aware order."""
        if not os.path.isdir(project_path):
            return f"Project path not found: {project_path}"

        code_context = []
        for root, dirnames, filenames in os.walk(project_path):
            dirnames[:] = sorted(
                dirname for dirname in dirnames
                if not self.metadata.should_ignore_dir(dirname)
            )

            for filename in sorted(filenames):
                if not self.metadata.is_supported_file(filename):
                    continue

                filepath = os.path.join(root, filename)
                relative_path = os.path.relpath(filepath, project_path)
                try:
                    content = self.file_loader.load_text(filepath)
                    code_context.append(f"=== {relative_path} ===\n{content}")
                except Exception:
                    log.exception("Could not read repository file: %s", filepath)
                    code_context.append(f"=== {relative_path} === (could not read)")

        return "\n\n".join(code_context) if code_context else "No supported repository files found."


def read_codebase(project_path: str) -> str:
    """Compatibility helper for legacy callers."""
    return RepositoryScanner().scan(project_path)
