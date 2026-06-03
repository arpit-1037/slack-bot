"""Context optimization for hybrid repository retrieval."""

from __future__ import annotations

from dataclasses import replace

from src.hybrid_retrieval.retrieval_models import OptimizedContext, RankedCandidate
from src.repository.repository_indexer import FileIndexEntry
from src.repository.repository_state import RepositoryState
from src.retrieval.context_assembler import ContextAssembler
from src.retrieval.retrieval_models import CodeSnippet, RankedFile, RankedSymbol, RetrievalContext
from src.utils.helpers import int_env


class ContextOptimizer:
    """Select the smallest useful context package from ranked retrieval output."""

    def __init__(
        self,
        context_assembler: ContextAssembler | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
        max_snippets: int | None = None,
        max_total_chars: int | None = None,
    ) -> None:
        self.max_files = max_files or int_env("HYBRID_RETRIEVAL_MAX_FILES", 6, 1)
        self.max_symbols = max_symbols or int_env("HYBRID_RETRIEVAL_MAX_SYMBOLS", 12, 1)
        self.max_snippets = max_snippets or int_env("HYBRID_RETRIEVAL_MAX_SNIPPETS", 12, 1)
        self.max_total_chars = max_total_chars or int_env("RETRIEVAL_MAX_CONTEXT_CHARS", 24_000, 2_000)
        self.context_assembler = context_assembler or ContextAssembler(
            max_snippets=self.max_snippets,
            max_total_chars=self.max_total_chars,
        )

    def optimize_context(
        self,
        query: str,
        repository_index: dict[str, FileIndexEntry],
        ranked_files: list[RankedFile],
        ranked_symbols: list[RankedSymbol],
        repository_state: RepositoryState | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
    ) -> OptimizedContext:
        """Build a compact context package from ranked files and symbols."""
        files = ranked_files[: (max_files or self.max_files)]
        selected_paths = {file.path for file in files}
        symbols = [
            symbol
            for symbol in ranked_symbols
            if symbol.file_path in selected_paths
        ][: (max_symbols or self.max_symbols)]
        context = self.context_assembler.assemble(
            query=query,
            repository_index=repository_index,
            ranked_files=files,
            ranked_symbols=symbols,
            repository_state=repository_state,
        )
        context = replace(context, snippets=context.snippets[: self.max_snippets])
        optimized = OptimizedContext(
            query=query,
            files=context.files,
            symbols=context.symbols,
            snippets=context.snippets,
            total_chars=len(context.format_context(max_chars=self.max_total_chars)),
            ranking_decisions=context.ranking_decisions,
            repository_summary=context.repository_summary,
            context=context,
        )
        return self.reduce_context_size(optimized, max_chars=self.max_total_chars)

    def reduce_context_size(
        self,
        optimized_context: OptimizedContext,
        max_chars: int | None = None,
    ) -> OptimizedContext:
        """Trim snippets until formatted context fits the configured size."""
        max_chars = max_chars or self.max_total_chars
        context = optimized_context.context
        if context is None:
            return optimized_context
        if len(context.format_context()) <= max_chars:
            return optimized_context

        snippets: list[CodeSnippet] = []
        for snippet in optimized_context.snippets:
            candidate_context = replace(context, snippets=[*snippets, snippet])
            if len(candidate_context.format_context()) > max_chars and snippets:
                break
            snippets.append(snippet)

        reduced_context = replace(context, snippets=snippets)
        return replace(
            optimized_context,
            snippets=snippets,
            total_chars=len(reduced_context.format_context(max_chars=max_chars)),
            context=reduced_context,
        )

    def select_best_candidates(
        self,
        candidates: list[RankedCandidate],
        max_files: int | None = None,
    ) -> list[RankedCandidate]:
        """Select one best candidate per file, preserving hybrid ranking order."""
        selected: list[RankedCandidate] = []
        seen_paths: set[str] = set()
        for candidate in sorted(candidates, key=lambda item: (-item.score.final_score, item.file_path)):
            if candidate.file_path in seen_paths:
                continue
            selected.append(candidate)
            seen_paths.add(candidate.file_path)
            if len(selected) >= (max_files or self.max_files):
                break
        return selected


def optimize_context(
    query: str,
    repository_index: dict[str, FileIndexEntry],
    ranked_files: list[RankedFile],
    ranked_symbols: list[RankedSymbol],
    repository_state: RepositoryState | None = None,
) -> OptimizedContext:
    """Build optimized retrieval context using default limits."""
    return ContextOptimizer().optimize_context(
        query=query,
        repository_index=repository_index,
        ranked_files=ranked_files,
        ranked_symbols=ranked_symbols,
        repository_state=repository_state,
    )


def reduce_context_size(optimized_context: OptimizedContext, max_chars: int | None = None) -> OptimizedContext:
    """Reduce optimized context to fit the requested character budget."""
    return ContextOptimizer().reduce_context_size(optimized_context, max_chars=max_chars)


def select_best_candidates(
    candidates: list[RankedCandidate],
    max_files: int | None = None,
) -> list[RankedCandidate]:
    """Select one best candidate per file."""
    return ContextOptimizer().select_best_candidates(candidates, max_files=max_files)
