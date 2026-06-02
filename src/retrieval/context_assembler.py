"""Focused retrieval context assembly from ranked repository signals."""

from __future__ import annotations

from src.repository.repository_indexer import FileIndexEntry
from src.repository.repository_state import RepositoryState
from src.retrieval.retrieval_models import CodeSnippet, RankedFile, RankedSymbol, RetrievalContext
from src.utils.helpers import int_env


class ContextAssembler:
    """Assemble the smallest useful context package from ranked files and symbols."""

    def __init__(
        self,
        max_snippets: int | None = None,
        snippet_radius: int | None = None,
        max_snippet_chars: int | None = None,
        max_total_chars: int | None = None,
        max_symbols_per_file: int | None = None,
        max_file_excerpt_chars: int | None = None,
    ) -> None:
        self.max_snippets = max_snippets or int_env("RETRIEVAL_MAX_SNIPPETS", 12, 1)
        self.snippet_radius = snippet_radius or int_env("RETRIEVAL_SNIPPET_RADIUS", 8, 1)
        self.max_snippet_chars = max_snippet_chars or int_env("RETRIEVAL_MAX_SNIPPET_CHARS", 2_400, 300)
        self.max_total_chars = max_total_chars or int_env("RETRIEVAL_MAX_CONTEXT_CHARS", 24_000, 2_000)
        self.max_symbols_per_file = max_symbols_per_file or int_env("RETRIEVAL_MAX_SYMBOLS_PER_FILE", 3, 1)
        self.max_file_excerpt_chars = max_file_excerpt_chars or int_env("RETRIEVAL_MAX_FILE_EXCERPT_CHARS", 3_000, 500)

    def assemble(
        self,
        query: str,
        repository_index: dict[str, FileIndexEntry],
        ranked_files: list[RankedFile],
        ranked_symbols: list[RankedSymbol],
        repository_state: RepositoryState | None = None,
    ) -> RetrievalContext:
        """Build focused context from ranked files, symbols, snippets, and edges."""
        snippets = self._select_snippets(repository_index, ranked_files, ranked_symbols)
        context = RetrievalContext(
            query=query,
            files=ranked_files,
            symbols=ranked_symbols,
            snippets=snippets,
            dependency_edges=self._dependency_edges(ranked_files),
            repository_summary=repository_state.as_summary_dict() if repository_state else {},
            ranking_decisions=self._ranking_decisions(ranked_files, ranked_symbols),
        )
        bounded = context.format_context(max_chars=self.max_total_chars)
        if len(bounded) == len(context.format_context()):
            return context
        return RetrievalContext(
            query=context.query,
            files=context.files,
            symbols=context.symbols,
            snippets=self._fit_snippets(context.snippets),
            dependency_edges=context.dependency_edges,
            repository_summary=context.repository_summary,
            ranking_decisions=context.ranking_decisions,
        )

    def _select_snippets(
        self,
        repository_index: dict[str, FileIndexEntry],
        ranked_files: list[RankedFile],
        ranked_symbols: list[RankedSymbol],
    ) -> list[CodeSnippet]:
        """Select symbol snippets, nearby imports, and small fallbacks."""
        snippets: list[CodeSnippet] = []
        symbols_by_file: dict[str, list[RankedSymbol]] = {}
        for symbol in ranked_symbols:
            symbols_by_file.setdefault(symbol.file_path, []).append(symbol)

        for ranked_file in ranked_files:
            entry = repository_index.get(ranked_file.path)
            if entry is None:
                continue
            file_symbols = symbols_by_file.get(ranked_file.path, [])[: self.max_symbols_per_file]
            if file_symbols:
                import_snippet = self._imports_snippet(ranked_file.path, entry)
                if import_snippet is not None:
                    snippets.append(import_snippet)
                for symbol in file_symbols:
                    snippets.append(self._symbol_snippet(symbol, entry))
            else:
                snippets.append(self._file_excerpt_snippet(ranked_file.path, entry))

        return self._dedupe_snippets(snippets)[: self.max_snippets]

    def _imports_snippet(self, path: str, entry: FileIndexEntry) -> CodeSnippet | None:
        """Return a compact snippet for import lines."""
        imports = entry["symbols"]["imports"][:8]
        if not imports:
            return None
        line_start = min(int(item.get("line_start") or 1) for item in imports)
        line_end = max(int(item.get("line_end") or line_start) for item in imports)
        content = self._numbered_slice(entry.get("content", ""), line_start, line_end)
        return CodeSnippet(
            file_path=path,
            line_start=line_start,
            line_end=line_end,
            content=self._truncate(content),
            reason="nearby-imports",
        )

    def _symbol_snippet(self, symbol: RankedSymbol, entry: FileIndexEntry) -> CodeSnippet:
        """Return a bounded snippet around a selected symbol."""
        lines = entry.get("content", "").splitlines()
        total_lines = max(len(lines), 1)
        start = max(1, symbol.line_start - self.snippet_radius)
        end = min(total_lines, symbol.line_end + self.snippet_radius)
        content = self._numbered_slice(entry.get("content", ""), start, end)
        return CodeSnippet(
            file_path=symbol.file_path,
            line_start=start,
            line_end=end,
            content=self._truncate(content),
            reason=f"ranked-{symbol.kind}",
            symbol_name=symbol.name,
            source_metadata={"score": symbol.score, "reasons": symbol.reasons},
        )

    def _file_excerpt_snippet(self, path: str, entry: FileIndexEntry) -> CodeSnippet:
        """Return a small file excerpt when no symbol-level match exists."""
        content = entry.get("content", "")
        lines = content.splitlines()
        excerpt = content[: self.max_file_excerpt_chars].rstrip()
        if len(content) > self.max_file_excerpt_chars:
            excerpt += "\n... [file excerpt truncated]"
        return CodeSnippet(
            file_path=path,
            line_start=1,
            line_end=max(1, min(len(lines) or 1, excerpt.count("\n") + 1)),
            content=excerpt,
            reason="ranked-file-excerpt",
        )

    def _numbered_slice(self, content: str, line_start: int, line_end: int) -> str:
        """Return line-numbered source lines from a bounded range."""
        lines = content.splitlines()
        if not lines:
            return ""
        line_start = max(1, line_start)
        line_end = min(len(lines), line_end)
        return "\n".join(
            f"{number}: {lines[number - 1]}"
            for number in range(line_start, line_end + 1)
        )

    def _truncate(self, content: str) -> str:
        """Trim one snippet to the configured per-snippet budget."""
        if len(content) <= self.max_snippet_chars:
            return content
        suffix = "\n... [snippet truncated]"
        return content[: self.max_snippet_chars - len(suffix)].rstrip() + suffix

    def _dedupe_snippets(self, snippets: list[CodeSnippet]) -> list[CodeSnippet]:
        """Drop duplicate file/range snippets while preserving stable order."""
        seen: set[tuple[str, int, int, str | None]] = set()
        unique = []
        for snippet in snippets:
            key = (snippet.file_path, snippet.line_start, snippet.line_end, snippet.symbol_name)
            if key in seen:
                continue
            seen.add(key)
            unique.append(snippet)
        return unique

    def _fit_snippets(self, snippets: list[CodeSnippet]) -> list[CodeSnippet]:
        """Keep snippets under the total context budget in deterministic order."""
        fitted = []
        used = 0
        for snippet in snippets:
            cost = len(snippet.content) + 160
            if fitted and used + cost > self.max_total_chars:
                break
            fitted.append(snippet)
            used += cost
        return fitted

    def _dependency_edges(self, ranked_files: list[RankedFile]) -> list[str]:
        """Return dependency edges among selected files."""
        selected_paths = {file.path for file in ranked_files}
        edges = []
        for file in ranked_files:
            for dependency in file.dependencies:
                if dependency in selected_paths:
                    edges.append(f"{file.path} -> {dependency}")
        return sorted(set(edges))

    def _ranking_decisions(
        self,
        ranked_files: list[RankedFile],
        ranked_symbols: list[RankedSymbol],
    ) -> list[str]:
        """Summarize why top files and symbols were selected."""
        decisions = []
        for file in ranked_files[:5]:
            reasons = ", ".join(file.reasons[:5]) or "no explicit reason"
            decisions.append(f"{file.path} scored {file.score} from {reasons}")
        for symbol in ranked_symbols[:5]:
            reasons = ", ".join(symbol.reasons[:5]) or "file context"
            decisions.append(f"{symbol.name} in {symbol.file_path} scored {symbol.score} from {reasons}")
        return decisions
