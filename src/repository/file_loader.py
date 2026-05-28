"""File loading helpers for repository scans."""

from __future__ import annotations


class FileLoader:
    """Read repository files as UTF-8 text."""

    def load_text(self, path: str) -> str:
        """Read a text file and let callers decide how to handle failures."""
        with open(path, "r", encoding="utf-8") as file:
            return file.read()
