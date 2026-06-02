"""Deterministic symbol ranking for repository retrieval."""

from __future__ import annotations

from typing import Any

from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry
from src.repository.repository_state import RepositoryState
from src.retrieval.file_ranker import query_terms
from src.retrieval.retrieval_models import RankedFile, RankedSymbol


class SymbolRanker:
    """Rank functions, classes, and methods using query and file relevance."""

    def rank_symbols(
        self,
        user_query: str,
        repository_index: dict[str, FileIndexEntry],
        ranked_files: list[RankedFile],
        dependency_mapper: DependencyMapper | None = None,
        repository_state: RepositoryState | None = None,
        limit: int | None = None,
    ) -> list[RankedSymbol]:
        """Return repository symbols ranked by deterministic relevance score."""
        terms = query_terms(user_query)
        file_scores = {file.path: file.score for file in ranked_files}
        selected_paths = set(file_scores)
        ranked: list[RankedSymbol] = []

        for path in sorted(selected_paths):
            entry = repository_index.get(path)
            if entry is None:
                continue
            for function in entry["symbols"]["functions"]:
                ranked.append(
                    self.score_symbol(
                        user_query=user_query,
                        symbol=function,
                        kind="function",
                        file_path=path,
                        file_score=file_scores.get(path, 0),
                        terms=terms,
                        dependency_mapper=dependency_mapper,
                        repository_state=repository_state,
                    )
                )
            for class_info in entry["symbols"]["classes"]:
                ranked.append(
                    self.score_symbol(
                        user_query=user_query,
                        symbol=class_info,
                        kind="class",
                        file_path=path,
                        file_score=file_scores.get(path, 0),
                        terms=terms,
                        dependency_mapper=dependency_mapper,
                        repository_state=repository_state,
                    )
                )
                for method in class_info.get("methods", []):
                    method_metadata = dict(method)
                    method_metadata["class"] = class_info.get("name", "")
                    ranked.append(
                        self.score_symbol(
                            user_query=user_query,
                            symbol=method_metadata,
                            kind="method",
                            file_path=path,
                            file_score=file_scores.get(path, 0),
                            terms=terms,
                            dependency_mapper=dependency_mapper,
                            repository_state=repository_state,
                        )
                    )

        ranked = [symbol for symbol in ranked if symbol.score > 0]
        ranked.sort(key=lambda symbol: (-symbol.score, symbol.file_path, symbol.line_start, symbol.name))
        return ranked[:limit] if limit is not None else ranked

    def score_symbol(
        self,
        user_query: str,
        symbol: dict[str, Any],
        kind: str,
        file_path: str,
        file_score: int = 0,
        terms: set[str] | None = None,
        dependency_mapper: DependencyMapper | None = None,
        repository_state: RepositoryState | None = None,
    ) -> RankedSymbol:
        """Score one symbol and return explainable ranking metadata."""
        del user_query
        terms = terms if terms is not None else set()
        name = str(symbol.get("name", ""))
        name_text = _searchable(name)
        doc_text = _searchable(symbol.get("docstring") or "")
        argument_text = _searchable(" ".join(symbol.get("arguments", [])))
        score = 0
        reasons: list[str] = []

        for term in sorted(terms):
            if term == name_text or term in name_text.split():
                score += 20
                reasons.append(f"name:{term}")
            elif term in name_text:
                score += 12
                reasons.append(f"name-fragment:{term}")
            elif term in doc_text:
                score += 5
                reasons.append(f"docstring:{term}")
            elif term in argument_text:
                score += 3
                reasons.append(f"argument:{term}")

        file_relevance = min(file_score // 4, 25)
        if file_relevance:
            score += file_relevance
            reasons.append("file-relevance")

        if dependency_mapper is not None:
            dependency_count = len(dependency_mapper.get_dependencies(file_path))
            dependent_count = len(dependency_mapper.get_dependents(file_path))
            if dependency_count:
                score += min(dependency_count, 3)
                reasons.append("dependency-context")
            if dependent_count:
                score += min(dependent_count, 3)
                reasons.append("dependent-context")

        if repository_state is not None:
            if file_path in repository_state.changed_files:
                score += 8
                reasons.append("git:changed")
            if file_path in repository_state.staged_files:
                score += 6
                reasons.append("git:staged")
            if file_path in repository_state.untracked_files:
                score += 5
                reasons.append("git:untracked")

        if score == 0 and file_score > 0:
            score = max(1, min(file_score // 10, 6))
            reasons.append("file-context")

        return RankedSymbol(
            name=name,
            kind=kind,
            file_path=file_path,
            score=score,
            line_start=int(symbol.get("line_start") or 1),
            line_end=int(symbol.get("line_end") or symbol.get("line_start") or 1),
            reasons=sorted(set(reasons)),
            source_metadata={
                "docstring": symbol.get("docstring"),
                "arguments": list(symbol.get("arguments", [])),
                "class": symbol.get("class"),
            },
        )


def _searchable(value: Any) -> str:
    """Return a simple lowercase symbol search string."""
    text = str(value).replace("_", " ")
    return " ".join(text.lower().split())
