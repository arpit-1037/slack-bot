"""Recursive repository scanner that returns structured file metadata."""

from __future__ import annotations

import os
from typing import TypedDict

from src.repository.file_loader import FileLoader
from src.repository.metadata_extractor import MetadataExtractor
from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)


class ScannedFile(TypedDict, total=False):
    """Structured metadata for one scanned repository file."""

    path: str
    extension: str
    size: int
    content: str
    truncated: bool
    skipped_reason: str


class RepositoryScanner:
    """Scan supported repository files while excluding heavyweight directories."""

    def __init__(
        self,
        file_loader: FileLoader | None = None,
        metadata: MetadataExtractor | None = None,
        max_file_bytes: int | None = None,
    ) -> None:
        self.file_loader = file_loader or FileLoader()
        self.metadata = metadata or MetadataExtractor()
        self.max_file_bytes = max_file_bytes or int_env(
            "REPOSITORY_SCAN_MAX_FILE_BYTES",
            200_000,
            minimum=1_000,
        )

    def scan(self, project_path: str) -> list[ScannedFile]:
        """Return supported files as structured metadata in stable path order."""
        if not os.path.isdir(project_path):
            log.warning("Repository path not found: %s", project_path)
            return []

        project_path = os.path.abspath(project_path)
        scanned_files: list[ScannedFile] = []

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
                extension = filename if filename in {".env.example"} else os.path.splitext(filename)[1]

                try:
                    size = os.path.getsize(filepath)
                except OSError as error:
                    log.warning("Could not stat repository file %s: %s", filepath, error)
                    scanned_files.append({
                        "path": relative_path,
                        "extension": extension,
                        "size": 0,
                        "content": "",
                        "skipped_reason": "stat_failed",
                    })
                    continue

                try:
                    content, truncated = self.file_loader.load_text(
                        filepath,
                        max_bytes=self.max_file_bytes,
                    )
                    scanned_files.append({
                        "path": relative_path,
                        "extension": extension,
                        "size": size,
                        "content": content,
                        "truncated": truncated,
                    })
                except Exception as error:
                    log.warning("Could not read repository file %s: %s", filepath, error)
                    scanned_files.append({
                        "path": relative_path,
                        "extension": extension,
                        "size": size,
                        "content": "",
                        "skipped_reason": "read_failed",
                    })

        log.info(
            "Scanned repository path=%s files=%d max_file_bytes=%d",
            project_path,
            len(scanned_files),
            self.max_file_bytes,
        )
        return scanned_files

    def format_context(self, files: list[ScannedFile]) -> str:
        """Format scanned files for legacy prompt context consumers."""
        if not files:
            return "No supported repository files found."

        chunks = []
        for file_info in files:
            path = file_info["path"]
            suffix = " (truncated)" if file_info.get("truncated") else ""
            skipped = file_info.get("skipped_reason")
            if skipped:
                chunks.append(f"=== {path} === ({skipped})")
            else:
                chunks.append(f"=== {path}{suffix} ===\n{file_info.get('content', '')}")
        return "\n\n".join(chunks)


def read_codebase(project_path: str) -> str:
    """Compatibility helper for legacy callers that need a prompt string."""
    if not os.path.isdir(project_path):
        return f"Project path not found: {project_path}"
    scanner = RepositoryScanner()
    return scanner.format_context(scanner.scan(project_path))
