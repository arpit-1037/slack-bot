"""Repository chunking for semantic embedding."""

from __future__ import annotations

import ast
import hashlib
import re
from typing import Iterable

from src.embeddings.embedding_models import CodeChunk
from src.repository.repository_indexer import FileIndexEntry
from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)


class CodeChunker:
    """Create meaningful semantic chunks from indexed repository files."""

    def __init__(
        self,
        max_chunk_chars: int | None = None,
        fallback_lines: int | None = None,
    ) -> None:
        self.max_chunk_chars = max_chunk_chars or int_env("EMBEDDING_MAX_CHUNK_CHARS", 2_400, 300)
        self.fallback_lines = fallback_lines or int_env("EMBEDDING_FALLBACK_LINES", 80, 10)

    def chunk_file(self, path: str, entry: FileIndexEntry | dict) -> list[CodeChunk]:
        """Return semantic chunks for one indexed file."""
        content = str(entry.get("content", ""))
        if not content.strip() or bool(entry.get("truncated", False)):
            return []

        extension = str(entry.get("extension", ""))
        if extension == ".py" or path.endswith(".py"):
            chunks = self._python_chunks(path, content)
        else:
            chunks = self._text_chunks(path, content)

        log.info("Chunked file path=%s chunks=%d", path, len(chunks))
        return chunks

    def chunk_repository(self, repository_index: dict[str, FileIndexEntry]) -> list[CodeChunk]:
        """Return chunks for every supported file in a repository index."""
        chunks = [
            chunk
            for path, entry in sorted(repository_index.items())
            for chunk in self.chunk_file(path, entry)
        ]
        log.info("Chunked repository files=%d chunks=%d", len(repository_index), len(chunks))
        return chunks

    def _python_chunks(self, path: str, content: str) -> list[CodeChunk]:
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            log.warning("Could not parse Python file for embedding chunks path=%s", path)
            return self._text_chunks(path, content)

        chunks: list[CodeChunk] = []
        lines = content.splitlines()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                chunks.append(self._node_chunk(path, lines, node, "class", node.name))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chunks.append(
                            self._node_chunk(path, lines, child, "method", f"{node.name}.{child.name}")
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(self._node_chunk(path, lines, node, "function", node.name))

        chunks.extend(self._doc_comment_chunks(path, lines))
        if not chunks:
            chunks.extend(self._fallback_chunks(path, lines, "python-fallback"))
        return self._dedupe_chunks(self._split_large_chunks(chunks))

    def _text_chunks(self, path: str, content: str) -> list[CodeChunk]:
        lines = content.splitlines()
        doc_chunks = self._doc_comment_chunks(path, lines)
        if doc_chunks:
            return self._dedupe_chunks(self._split_large_chunks(doc_chunks))
        return self._fallback_chunks(path, lines, "text-fallback")

    def _node_chunk(
        self,
        path: str,
        lines: list[str],
        node: ast.AST,
        chunk_type: str,
        symbol_name: str,
    ) -> CodeChunk:
        line_start = int(getattr(node, "lineno", 1))
        line_end = int(getattr(node, "end_lineno", line_start))
        content = "\n".join(lines[line_start - 1:line_end])
        docstring = ast.get_docstring(node) or ""
        return CodeChunk(
            chunk_id=self._chunk_id(path, line_start, line_end, chunk_type, symbol_name),
            file_path=path,
            content=content,
            chunk_type=chunk_type,
            symbol_name=symbol_name,
            line_start=line_start,
            line_end=line_end,
            metadata={
                "docstring": docstring,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )

    def _doc_comment_chunks(self, path: str, lines: list[str]) -> list[CodeChunk]:
        chunks = []
        current: list[tuple[int, str]] = []

        def flush(chunk_type: str = "documentation") -> None:
            nonlocal current
            if not current:
                return
            text = "\n".join(line for _, line in current).strip()
            if len(text) >= 24:
                line_start = current[0][0]
                line_end = current[-1][0]
                chunks.append(
                    CodeChunk(
                        chunk_id=self._chunk_id(path, line_start, line_end, chunk_type, None),
                        file_path=path,
                        content=text,
                        chunk_type=chunk_type,
                        line_start=line_start,
                        line_end=line_end,
                        metadata={
                            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        },
                    )
                )
            current = []

        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "/*", "*", "*/")):
                current.append((number, stripped))
                continue
            if re.match(r'^[rubfRUBF]*("""|\'\'\')', stripped):
                current.append((number, stripped))
                flush("documentation")
                continue
            flush()
        flush()
        return chunks

    def _fallback_chunks(self, path: str, lines: list[str], chunk_type: str) -> list[CodeChunk]:
        chunks = []
        for start_index in range(0, len(lines), self.fallback_lines):
            selected = lines[start_index:start_index + self.fallback_lines]
            text = "\n".join(selected).strip()
            if not text:
                continue
            line_start = start_index + 1
            line_end = start_index + len(selected)
            chunks.append(
                CodeChunk(
                    chunk_id=self._chunk_id(path, line_start, line_end, chunk_type, None),
                    file_path=path,
                    content=self._truncate(text),
                    chunk_type=chunk_type,
                    line_start=line_start,
                    line_end=line_end,
                    metadata={
                        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    },
                )
            )
        return chunks

    def _split_large_chunks(self, chunks: Iterable[CodeChunk]) -> list[CodeChunk]:
        split_chunks = []
        for chunk in chunks:
            if len(chunk.content) <= self.max_chunk_chars:
                split_chunks.append(chunk)
                continue
            lines = chunk.content.splitlines()
            for index in range(0, len(lines), self.fallback_lines):
                selected = lines[index:index + self.fallback_lines]
                content = self._truncate("\n".join(selected))
                if not content.strip():
                    continue
                line_start = chunk.line_start + index
                line_end = line_start + len(selected) - 1
                split_chunks.append(
                    CodeChunk(
                        chunk_id=self._chunk_id(
                            chunk.file_path,
                            line_start,
                            line_end,
                            chunk.chunk_type,
                            chunk.symbol_name,
                        ),
                        file_path=chunk.file_path,
                        content=content,
                        chunk_type=chunk.chunk_type,
                        symbol_name=chunk.symbol_name,
                        line_start=line_start,
                        line_end=line_end,
                        metadata=chunk.metadata,
                    )
                )
        return split_chunks

    def _dedupe_chunks(self, chunks: list[CodeChunk]) -> list[CodeChunk]:
        seen: set[str] = set()
        unique = []
        for chunk in chunks:
            fingerprint = chunk.metadata.get("content_sha256") or hashlib.sha256(
                chunk.content.encode("utf-8")
            ).hexdigest()
            key = f"{chunk.file_path}:{fingerprint}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(chunk)
        return unique

    def _truncate(self, content: str) -> str:
        if len(content) <= self.max_chunk_chars:
            return content
        suffix = "\n... [chunk truncated]"
        return content[: self.max_chunk_chars - len(suffix)].rstrip() + suffix

    def _chunk_id(
        self,
        path: str,
        line_start: int,
        line_end: int,
        chunk_type: str,
        symbol_name: str | None,
    ) -> str:
        payload = f"{path}:{line_start}:{line_end}:{chunk_type}:{symbol_name or ''}"
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
        return f"{path}:{line_start}-{line_end}:{digest}"
