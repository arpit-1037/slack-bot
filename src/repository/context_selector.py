"""Deterministic repository context selection for task prompts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.repository.repository_state import RepositoryState
from src.retrieval.context_assembler import ContextAssembler
from src.retrieval.file_ranker import query_terms as retrieval_query_terms
from src.retrieval.retrieval_engine import RepositoryRetrievalEngine
from src.retrieval.retrieval_models import RankedFile, RankedSymbol
from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "does", "for",
    "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "our", "please", "should", "that", "the", "this", "to", "what", "when",
    "where", "why", "with", "you",
}

QUERY_EXPANSIONS = {
    "auth": {"authentication", "authorize", "authorization", "login", "token", "jwt", "session"},
    "jwt": {"auth", "authentication", "login", "middleware", "token", "session"},
    "login": {"auth", "authentication", "jwt", "session", "token", "user"},
    "middleware": {"auth", "request", "response", "handler", "route"},
    "controller": {"route", "handler", "service", "request", "response"},
    "route": {"routes", "router", "controller", "handler", "endpoint"},
    "api": {"route", "router", "controller", "handler", "endpoint"},
    "redis": {"cache", "connection", "queue", "session"},
    "database": {"db", "model", "repository", "migration", "query"},
    "db": {"database", "model", "repository", "migration", "query"},
    "slack": {"event", "mention", "thread", "channel", "message"},
    "git": {"commit", "diff", "status", "branch"},
    "error": {"exception", "bug", "fail", "failing", "failed"},
    "failing": {"error", "exception", "bug", "failed"},
    "circular": {"import", "dependency", "cycle"},
}


@dataclass(frozen=True)
class SelectedFile:
    """One file selected for task context."""

    path: str
    score: int
    reasons: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextSelection:
    """Structured context selection result."""

    task: str
    selected_files: list[SelectedFile]
    context: str
    repository_summary: dict[str, Any] = field(default_factory=dict)


class ContextSelector:
    """Select minimal useful repository context for a task."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
        retrieval_engine: RepositoryRetrievalEngine | None = None,
        max_files: int | None = None,
        max_total_chars: int | None = None,
        max_file_chars: int | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()
        self.max_files = max_files or int_env("REPOSITORY_CONTEXT_MAX_FILES", 6, 1)
        self.max_total_chars = max_total_chars or int_env("REPOSITORY_CONTEXT_MAX_CHARS", 24_000, 2_000)
        self.max_file_chars = max_file_chars or int_env("REPOSITORY_CONTEXT_MAX_FILE_CHARS", 6_000, 1_000)
        self.retrieval_engine = retrieval_engine or RepositoryRetrievalEngine(
            indexer=self.indexer,
            dependency_mapper=self.dependency_mapper,
            context_assembler=ContextAssembler(
                max_total_chars=self.max_total_chars,
                max_snippet_chars=self.max_file_chars,
                max_file_excerpt_chars=self.max_file_chars,
            ),
            max_files=self.max_files,
        )
        self.indexer = self.retrieval_engine.indexer
        self.dependency_mapper = self.retrieval_engine.dependency_mapper

    def select_context(
        self,
        project_path: str,
        task: str,
        request_id: str | None = None,
    ) -> ContextSelection:
        """Build a compact repository context string for a task."""
        result = self.retrieval_engine.retrieve_context(
            project_path=project_path,
            query=task,
            request_id=request_id,
            max_files=self.max_files,
        )
        selected_files = [
            self._selected_file_from_retrieval(file, result.symbols)
            for file in result.files
        ]
        context = result.context.format_context(max_chars=self.max_total_chars)

        log.info(
            "request_id=%s selected repository context files=%d chars=%d branch=%s terms=%s",
            request_id,
            len(selected_files),
            len(context),
            result.context.repository_summary.get("metadata", {}).get("branch") or "unknown",
            ",".join(result.terms),
        )
        return ContextSelection(
            task=task,
            selected_files=selected_files,
            context=context,
            repository_summary=result.context.repository_summary,
        )

    def query_terms(self, task: str) -> set[str]:
        """Tokenize and expand a task into deterministic repository search terms."""
        return retrieval_query_terms(task)

    def _selected_file_from_retrieval(
        self,
        file: RankedFile,
        symbols: list[RankedSymbol],
    ) -> SelectedFile:
        """Convert retrieval file metadata to the legacy context-selection model."""
        file_symbols = [symbol for symbol in symbols if symbol.file_path == file.path]
        functions = []
        classes = []
        for symbol in file_symbols:
            if symbol.kind == "class":
                classes.append(symbol.name)
            elif symbol.kind == "method" and symbol.source_metadata.get("class"):
                functions.append(f"{symbol.source_metadata['class']}.{symbol.name}")
            else:
                functions.append(symbol.name)

        return SelectedFile(
            path=file.path,
            score=file.score,
            reasons=file.reasons,
            functions=sorted(set(functions)),
            classes=sorted(set(classes)),
        )

    def _score_files(
        self,
        index: dict[str, FileIndexEntry],
        terms: set[str],
    ) -> list[tuple[str, int, list[str]]]:
        """Score files by path, symbols, imports, and light content signals."""
        scored = []
        for path, entry in index.items():
            score = 0
            reasons: list[str] = []
            path_text = self._searchable_path(path)

            for term in terms:
                if term in path_text:
                    score += 8
                    reasons.append(f"path:{term}")

            symbol_text = self._symbol_text(entry)
            for term in terms:
                if term in symbol_text:
                    score += 6
                    reasons.append(f"symbol:{term}")

            import_text = " ".join(
                f"{item.get('module', '')} {item.get('name', '')}"
                for item in entry["symbols"]["imports"]
            ).lower()
            for term in terms:
                if term in import_text:
                    score += 3
                    reasons.append(f"import:{term}")

            content_sample = entry["content"][: self.max_file_chars].lower()
            for term in terms:
                if term in content_sample:
                    score += 1
                    reasons.append(f"content:{term}")

            if score > 0:
                scored.append((path, score, sorted(set(reasons))))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored

    def _select_paths(
        self,
        scored: list[tuple[str, int, list[str]]],
        index: dict[str, FileIndexEntry],
    ) -> list[tuple[str, int, list[str]]]:
        """Select top files plus immediate dependencies and dependents."""
        if not scored:
            return self._fallback_paths(index)

        selected: dict[str, tuple[int, list[str]]] = {}
        for path, score, reasons in scored[: self.max_files]:
            selected[path] = (score, reasons)
            for dependency in self.dependency_mapper.get_dependencies(path)[:2]:
                selected.setdefault(dependency, (max(score - 2, 1), [f"dependency-of:{path}"]))
            for dependent in self.dependency_mapper.get_dependents(path)[:2]:
                selected.setdefault(dependent, (max(score - 3, 1), [f"dependent-of:{path}"]))

        ranked = [
            (path, score, reasons)
            for path, (score, reasons) in selected.items()
            if path in index
        ]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return ranked[: self.max_files]

    def _fallback_paths(self, index: dict[str, FileIndexEntry]) -> list[tuple[str, int, list[str]]]:
        """Choose useful overview files when no task-specific match exists."""
        preferred_names = {"README.md", "app.py", "main.py", "index.js", "package.json"}
        preferred = [
            (path, 1, ["fallback:overview"])
            for path in sorted(index)
            if os.path.basename(path) in preferred_names
        ]
        if preferred:
            return preferred[: self.max_files]
        return [(path, 1, ["fallback:first-supported"]) for path in sorted(index)[: self.max_files]]

    def _selected_file(
        self,
        path: str,
        score: int,
        reasons: list[str],
        entry: FileIndexEntry,
        terms: set[str],
    ) -> SelectedFile:
        """Build selected-file metadata with matching symbols."""
        functions = [
            function["name"]
            for function in entry["symbols"]["functions"]
            if self._matches_terms(function["name"], terms)
        ]
        classes = [
            class_info["name"]
            for class_info in entry["symbols"]["classes"]
            if self._matches_terms(class_info["name"], terms)
        ]

        return SelectedFile(
            path=path,
            score=score,
            reasons=reasons,
            functions=functions,
            classes=classes,
        )

    def _format_context(
        self,
        selected_files: list[SelectedFile],
        index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
    ) -> str:
        """Format selected context for a generic prompt."""
        if not selected_files:
            return "\n\n".join(
                [
                    "REPOSITORY CONTEXT SELECTION",
                    self._format_repository_state(repository_state),
                    "No relevant repository context found.",
                ]
            )

        parts = ["REPOSITORY CONTEXT SELECTION", self._format_repository_state(repository_state)]
        for selected in selected_files:
            entry = index[selected.path]
            chunk = f"{self._file_summary(selected, entry)}\n{self._file_excerpt(entry)}".strip()
            parts.append(chunk)

        context = "\n\n".join(parts)
        if len(context) > self.max_total_chars:
            suffix = "\n... [repository context truncated]"
            return context[: self.max_total_chars - len(suffix)].rstrip() + suffix
        return context

    def _file_summary(self, selected: SelectedFile, entry: FileIndexEntry) -> str:
        """Format compact metadata for one selected file."""
        symbols = entry["symbols"]
        functions = ", ".join(function["name"] for function in symbols["functions"]) or "none"
        classes = ", ".join(class_info["name"] for class_info in symbols["classes"]) or "none"
        imports = ", ".join(
            import_info.get("module") or import_info.get("name", "")
            for import_info in symbols["imports"]
        ) or "none"

        return (
            f"=== {selected.path} ===\n"
            f"score: {selected.score}; reasons: {', '.join(selected.reasons)}\n"
            f"classes: {classes}\n"
            f"functions: {functions}\n"
            f"imports: {imports}"
        )

    def _format_repository_state(self, repository_state: RepositoryState) -> str:
        """Format repository state metadata for prompt context."""
        return (
            "Repository State:\n"
            f"path: {repository_state.repo_path}\n"
            f"branch: {repository_state.branch or 'unknown'}\n"
            f"head: {repository_state.head_commit[:12] if repository_state.head_commit else 'unavailable'}\n"
            f"indexed_at: {repository_state.indexed_at or 'never'}\n"
            f"files_indexed: {repository_state.file_count}\n"
            f"changed_files: {', '.join(repository_state.changed_files) or 'none'}\n"
            f"staged_files: {', '.join(repository_state.staged_files) or 'none'}\n"
            f"untracked_files: {', '.join(repository_state.untracked_files) or 'none'}"
        )

    def _file_excerpt(self, entry: FileIndexEntry) -> str:
        """Return a bounded file excerpt."""
        content = entry["content"]
        if len(content) <= self.max_file_chars:
            return content
        return content[: self.max_file_chars].rstrip() + "\n... [file truncated for context]"

    def _searchable_path(self, path: str) -> str:
        """Return a token-friendly representation of a path."""
        return re.sub(r"[^a-z0-9_]+", " ", path.lower())

    def _symbol_text(self, entry: FileIndexEntry) -> str:
        """Return searchable symbol text for scoring."""
        chunks = []
        for function in entry["symbols"]["functions"]:
            chunks.append(function["name"])
            chunks.append(function.get("docstring") or "")
        for class_info in entry["symbols"]["classes"]:
            chunks.append(class_info["name"])
            chunks.append(class_info.get("docstring") or "")
            for method in class_info.get("methods", []):
                chunks.append(method["name"])
                chunks.append(method.get("docstring") or "")
        return " ".join(chunks).lower()

    def _matches_terms(self, value: str, terms: set[str]) -> bool:
        """Return True when a symbol value matches any query term."""
        value = value.lower()
        return any(term in value for term in terms)
