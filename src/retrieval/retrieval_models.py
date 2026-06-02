"""Strongly typed models for deterministic repository retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RankedFile:
    """A repository file ranked for relevance to a user query."""

    path: str
    score: int
    reasons: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RankedSymbol:
    """A function, method, or class ranked for relevance to a user query."""

    name: str
    kind: str
    file_path: str
    score: int
    line_start: int
    line_end: int
    reasons: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeSnippet:
    """A bounded source snippet selected for focused LLM context."""

    file_path: str
    line_start: int
    line_end: int
    content: str
    reason: str
    symbol_name: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalContext:
    """Focused repository context assembled from ranked files and symbols."""

    query: str
    files: list[RankedFile]
    symbols: list[RankedSymbol]
    snippets: list[CodeSnippet]
    dependency_edges: list[str] = field(default_factory=list)
    repository_summary: dict[str, Any] = field(default_factory=dict)
    ranking_decisions: list[str] = field(default_factory=list)

    def format_context(self, max_chars: int | None = None) -> str:
        """Return a compact prompt-ready representation of retrieved context."""
        parts = ["REPOSITORY RETRIEVAL CONTEXT", f"query: {self.query}"]
        state = self._format_repository_state()
        if state:
            parts.append(state)

        if self.files:
            parts.append("Ranked files:\n" + "\n".join(self._format_file(file) for file in self.files))
        else:
            parts.append("Ranked files: none")

        if self.symbols:
            parts.append("Ranked symbols:\n" + "\n".join(self._format_symbol(symbol) for symbol in self.symbols))

        if self.dependency_edges:
            parts.append("Dependency edges:\n" + "\n".join(f"- {edge}" for edge in self.dependency_edges))

        if self.ranking_decisions:
            parts.append("Ranking decisions:\n" + "\n".join(f"- {decision}" for decision in self.ranking_decisions))

        if self.snippets:
            parts.append("Focused snippets:\n" + "\n\n".join(self._format_snippet(snippet) for snippet in self.snippets))
        else:
            parts.append("Focused snippets: none")

        context = "\n\n".join(parts)
        if max_chars is not None and len(context) > max_chars:
            suffix = "\n... [retrieval context truncated]"
            return context[: max_chars - len(suffix)].rstrip() + suffix
        return context

    def _format_file(self, file: RankedFile) -> str:
        """Format one ranked file line."""
        reasons = ", ".join(file.reasons) or "none"
        return f"- {file.path} score={file.score} reasons={reasons}"

    def _format_symbol(self, symbol: RankedSymbol) -> str:
        """Format one ranked symbol line."""
        reasons = ", ".join(symbol.reasons) or "none"
        return (
            f"- {symbol.name} ({symbol.kind}) {symbol.file_path}:"
            f"{symbol.line_start}-{symbol.line_end} score={symbol.score} reasons={reasons}"
        )

    def _format_snippet(self, snippet: CodeSnippet) -> str:
        """Format one selected source snippet."""
        symbol = f" symbol={snippet.symbol_name}" if snippet.symbol_name else ""
        header = f"=== {snippet.file_path}:{snippet.line_start}-{snippet.line_end} reason={snippet.reason}{symbol} ==="
        return f"{header}\n{snippet.content}"

    def _format_repository_state(self) -> str:
        """Format repository state summary if available."""
        metadata = self.repository_summary.get("metadata", {})
        statistics = self.repository_summary.get("statistics", {})
        git = self.repository_summary.get("git", {})
        if not metadata and not statistics and not git:
            return ""

        return (
            "Repository state:\n"
            f"branch: {metadata.get('branch') or 'unknown'}\n"
            f"head: {str(metadata.get('head_commit') or '')[:12] or 'unavailable'}\n"
            f"indexed_at: {metadata.get('indexed_at') or 'never'}\n"
            f"files_indexed: {statistics.get('file_count') or 0}\n"
            f"changed_files: {', '.join(git.get('changed_files') or []) or 'none'}\n"
            f"staged_files: {', '.join(git.get('staged_files') or []) or 'none'}\n"
            f"untracked_files: {', '.join(git.get('untracked_files') or []) or 'none'}"
        )


@dataclass(frozen=True)
class RetrievalResult:
    """Full retrieval result returned by the retrieval engine."""

    query: str
    terms: list[str]
    files: list[RankedFile]
    symbols: list[RankedSymbol]
    context: RetrievalContext

    @property
    def formatted_context(self) -> str:
        """Return prompt-ready focused repository context."""
        return self.context.format_context()
