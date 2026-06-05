"""Repository file metadata and inclusion rules."""

from __future__ import annotations

import os

IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    "vendor",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".repository_state",
    ".repository_memory",
}

SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".php",
    ".json",
    ".md",
    ".yaml",
    ".yml",
}

SUPPORTED_FILENAMES = {
    ".env.example",
}


class MetadataExtractor:
    """Decide which repository files should be included in context."""

    def should_ignore_dir(self, dirname: str) -> bool:
        """Return True when a directory should be skipped during recursive scans."""
        return dirname in IGNORED_DIRECTORIES or "pycache" in dirname.lower()

    def is_supported_file(self, filename: str) -> bool:
        """Return True when a file is supported by the repository scanner."""
        if filename in SUPPORTED_FILENAMES:
            return True
        _, extension = os.path.splitext(filename)
        return extension in SUPPORTED_EXTENSIONS
