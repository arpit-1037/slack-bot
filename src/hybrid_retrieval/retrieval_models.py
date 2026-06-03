"""Strongly typed models for hybrid repository retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.retrieval.retrieval_models import CodeSnippet, RankedFile, RankedSymbol, RetrievalContext, RetrievalResult

SIGNAL_KEYWORD = "keyword"
SIGNAL_DEPENDENCY = "dependency"
SIGNAL_SEMANTIC = "semantic"
SIGNAL_GIT = "git"


@dataclass(frozen=True)
class RetrievalSignal:
    """One retrieval signal collected for a repository candidate."""

    source: str
    raw_score: float
    reason: str
    file_path: str
    symbol_name: str | None = None
    normalized_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalScore:
    """Normalized signal scores and final fused ranking score."""

    keyword_score: float = 0.0
    dependency_score: float = 0.0
    semantic_score: float = 0.0
    git_score: float = 0.0
    final_score: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedCandidate:
    """A file, symbol, or snippet candidate before final context assembly."""

    candidate_id: str
    candidate_type: str
    file_path: str
    symbol_name: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    content: str | None = None
    retrieval_systems: list[str] = field(default_factory=list)
    signals: list[RetrievalSignal] = field(default_factory=list)
    score: RetrievalScore = field(default_factory=RetrievalScore)
    score_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OptimizedContext:
    """Smallest useful context package selected from hybrid-ranked results."""

    query: str
    files: list[RankedFile]
    symbols: list[RankedSymbol]
    snippets: list[CodeSnippet]
    total_chars: int
    ranking_decisions: list[str] = field(default_factory=list)
    repository_summary: dict[str, Any] = field(default_factory=dict)
    context: RetrievalContext | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe summary of optimized retrieval context."""
        return {
            "files": [file.path for file in self.files],
            "symbols": [symbol.name for symbol in self.symbols],
            "snippets": [
                {
                    "file_path": snippet.file_path,
                    "line_start": snippet.line_start,
                    "line_end": snippet.line_end,
                    "symbol_name": snippet.symbol_name,
                    "reason": snippet.reason,
                }
                for snippet in self.snippets
            ],
            "total_chars": self.total_chars,
            "ranking_decisions": list(self.ranking_decisions),
        }


@dataclass(frozen=True)
class HybridRetrievalResult:
    """Full result returned by the hybrid retrieval engine."""

    query: str
    terms: list[str]
    candidates: list[RankedCandidate]
    files: list[RankedFile]
    symbols: list[RankedSymbol]
    optimized_context: OptimizedContext
    explanations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def context(self) -> RetrievalContext:
        """Return prompt-ready retrieval context for compatibility callers."""
        if self.optimized_context.context is None:
            return RetrievalContext(
                query=self.query,
                files=self.files,
                symbols=self.symbols,
                snippets=self.optimized_context.snippets,
                repository_summary=self.optimized_context.repository_summary,
                ranking_decisions=self.optimized_context.ranking_decisions,
            )
        return self.optimized_context.context

    @property
    def formatted_context(self) -> str:
        """Return prompt-ready focused repository context."""
        return self.context.format_context()

    def to_retrieval_result(self) -> RetrievalResult:
        """Return the legacy retrieval result shape used by existing callers."""
        return RetrievalResult(
            query=self.query,
            terms=self.terms,
            files=self.files,
            symbols=self.symbols,
            context=self.context,
        )
