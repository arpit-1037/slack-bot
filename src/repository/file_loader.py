"""File loading helpers for repository scans."""

from __future__ import annotations


class FileLoader:
    """Read repository files with safe decoding."""

    def load_text(self, path: str, max_bytes: int | None = None) -> tuple[str, bool]:
        """Read a text file and return content plus a truncation flag.

        Files are decoded as UTF-8 with replacement so a single bad byte does not
        break repository indexing.
        """
        with open(path, "rb") as file:
            raw = file.read(max_bytes + 1 if max_bytes else -1)

        truncated = bool(max_bytes and len(raw) > max_bytes)
        if truncated:
            raw = raw[:max_bytes]

        return raw.decode("utf-8", errors="replace"), truncated
