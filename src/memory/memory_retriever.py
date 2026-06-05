"""Retrieve relevant repository facts from memory."""

from __future__ import annotations

import os
import re
from typing import Iterable

from src.memory.memory_indexer import MemoryIndexer
from src.memory.memory_models import MemoryEntry, MemoryQuery, MemoryResult, RepositoryFact, RepositoryMemory
from src.memory.memory_store import MemoryStore
from src.utils.helpers import get_logger

log = get_logger(__name__)

STOP_WORDS = {
    "are",
    "does",
    "handled",
    "handles",
    "how",
    "is",
    "the",
    "this",
    "where",
    "which",
    "with",
}


class MemoryRetriever:
    """Rank repository-memory facts for a natural language query."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        indexer: MemoryIndexer | None = None,
    ) -> None:
        self.store = store
        self.indexer = indexer or MemoryIndexer()

    def retrieve_memory(
        self,
        query: str | MemoryQuery,
        project_path: str | None = None,
        max_results: int | None = None,
        min_confidence: float | None = None,
        include_stale: bool | None = None,
    ) -> MemoryResult:
        """Retrieve ranked memory entries for a repository query."""
        memory_query = self._query(
            query,
            project_path=project_path,
            max_results=max_results,
            min_confidence=min_confidence,
            include_stale=include_stale,
        )
        memory = self._load_memory(memory_query.project_path)
        query_terms = self._terms(memory_query.query)
        entries = [
            entry
            for entry in (
                self._score_fact(fact, query_terms)
                for fact in memory.facts
                if memory_query.include_stale or fact.valid
            )
            if entry is not None
        ]
        entries.sort(key=lambda item: (-item.score, -item.fact.confidence, item.fact.key, item.fact.value))
        entries = entries[: memory_query.max_results]
        best = entries[0].score if entries else 0.0
        hit = bool(entries and best >= memory_query.min_confidence)
        result = MemoryResult(
            query=memory_query,
            entries=entries,
            hit=hit,
            best_confidence=round(best, 4),
            summary=self._summary(entries, hit),
            metadata={
                "repo_path": memory.repo_path,
                "repo_id": memory.repo_id,
                "fact_count": len(memory.facts),
                "memory_hit": hit,
            },
        )
        log.info(
            "repository_memory %s query=%r confidence=%.4f facts=%d",
            "hit" if hit else "miss",
            memory_query.query,
            result.best_confidence,
            len(entries),
        )
        return result

    def format_memory_result(self, result: MemoryResult) -> str:
        """Format a memory hit for Slack."""
        if not result.entries:
            return "*Repository Memory Miss*"
        best = result.entries[0]
        fact = best.fact
        lines = [
            "*Repository Memory Hit*" if result.hit else "*Repository Memory Candidate*",
            "",
            f"*Fact:* {fact.key}",
            f"*Answer:* {fact.value}",
            f"*Confidence:* {best.score:.2f}",
        ]
        if fact.file_path:
            lines.append(f"*File:* `{fact.file_path}`")
        if fact.symbol_name:
            lines.append(f"*Symbol:* `{fact.symbol_name}`")
        if best.reasons:
            lines.extend(["", "*Why:*", *(f"- {reason}" for reason in best.reasons[:4])])
        return "\n".join(lines)

    def _query(
        self,
        query: str | MemoryQuery,
        project_path: str | None,
        max_results: int | None,
        min_confidence: float | None,
        include_stale: bool | None,
    ) -> MemoryQuery:
        if isinstance(query, MemoryQuery):
            return MemoryQuery(
                query=query.query,
                project_path=project_path or query.project_path,
                max_results=max_results or query.max_results,
                min_confidence=min_confidence if min_confidence is not None else query.min_confidence,
                include_stale=include_stale if include_stale is not None else query.include_stale,
            )
        return MemoryQuery(
            query=str(query or ""),
            project_path=os.path.abspath(os.path.expanduser(project_path or ".")),
            max_results=max_results or 5,
            min_confidence=min_confidence if min_confidence is not None else 0.85,
            include_stale=bool(include_stale),
        )

    def _load_memory(self, project_path: str) -> RepositoryMemory:
        if self.store is not None:
            return self.store.load_memory()
        return MemoryStore(project_path).load_memory()

    def _score_fact(self, fact: RepositoryFact, query_terms: set[str]) -> MemoryEntry | None:
        fact_terms = self._terms(
            " ".join(
                [
                    fact.fact_type,
                    fact.key,
                    fact.value,
                    fact.file_path,
                    fact.symbol_name,
                    " ".join(fact.tags),
                    " ".join(fact.evidence),
                ]
            )
        )
        if not query_terms or not fact_terms:
            return None
        overlap = query_terms & fact_terms
        if not overlap:
            return None
        overlap_ratio = len(overlap) / max(len(query_terms), 1)
        path_bonus = 0.08 if fact.file_path and {"module", "file", "logic"} & query_terms else 0.0
        architecture_bonus = 0.06 if fact.fact_type in {"architecture", "entry_point", "framework", "database"} else 0.0
        score = fact.confidence * min(1.0, 0.48 + overlap_ratio * 0.58 + path_bonus + architecture_bonus)
        reasons = [f"matched terms: {', '.join(sorted(overlap))}"]
        if fact.file_path:
            reasons.append(f"repository file: {fact.file_path}")
        if fact.source:
            reasons.append(f"source: {fact.source}")
        return MemoryEntry(fact=fact, score=round(score, 4), reasons=reasons)

    def _summary(self, entries: list[MemoryEntry], hit: bool) -> str:
        if not entries:
            return "No repository memory matched the query."
        label = "hit" if hit else "candidate"
        best = entries[0]
        return f"Repository memory {label}: {best.fact.key} -> {best.fact.value}"

    def _terms(self, text: str) -> set[str]:
        return {
            term
            for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())
            if term not in STOP_WORDS
        }


_default_retriever: MemoryRetriever | None = None


def default_memory_retriever() -> MemoryRetriever:
    """Return a lazily created memory retriever."""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = MemoryRetriever()
    return _default_retriever


def retrieve_memory(
    query: str | MemoryQuery,
    project_path: str | None = None,
    max_results: int | None = None,
    min_confidence: float | None = None,
) -> MemoryResult:
    """Retrieve repository memory using the default retriever."""
    return default_memory_retriever().retrieve_memory(
        query,
        project_path=project_path,
        max_results=max_results,
        min_confidence=min_confidence,
    )
