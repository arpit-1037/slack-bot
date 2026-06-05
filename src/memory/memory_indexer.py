"""Search indexes and relationship graph builders for repository memory."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from src.memory.memory_models import RepositoryMemory


class MemoryIndexer:
    """Build deterministic indexes over repository memory."""

    def index_memory(self, memory: RepositoryMemory) -> dict[str, Any]:
        """Create searchable indexes for files, modules, symbols, facts, and relationships."""
        term_index: dict[str, list[str]] = defaultdict(list)
        files: dict[str, list[str]] = defaultdict(list)
        symbols: dict[str, list[str]] = defaultdict(list)
        fact_types: dict[str, list[str]] = defaultdict(list)

        for fact in memory.active_facts:
            fact_types[fact.fact_type].append(fact.id)
            if fact.file_path:
                files[fact.file_path].append(fact.id)
            if fact.symbol_name:
                symbols[fact.symbol_name.lower()].append(fact.id)
            for term in self._terms(" ".join([fact.key, fact.value, fact.file_path, fact.symbol_name, " ".join(fact.tags)])):
                if fact.id not in term_index[term]:
                    term_index[term].append(fact.id)

        graph = self.build_relationship_graph(memory)
        return {
            "terms": dict(sorted(term_index.items())),
            "files": dict(sorted(files.items())),
            "symbols": dict(sorted(symbols.items())),
            "fact_types": dict(sorted(fact_types.items())),
            "relationships": graph,
        }

    def build_relationship_graph(self, memory: RepositoryMemory) -> dict[str, list[dict[str, Any]]]:
        """Build adjacency lists for repository knowledge relationships."""
        graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relationship in memory.active_relationships:
            graph[relationship.source].append(
                {
                    "target": relationship.target,
                    "relationship_type": relationship.relationship_type,
                    "confidence": relationship.confidence,
                    "source_path": relationship.source_path,
                    "target_path": relationship.target_path,
                    "evidence": list(relationship.evidence),
                }
            )
        return {node: sorted(edges, key=lambda item: (-item["confidence"], item["target"])) for node, edges in graph.items()}

    def _terms(self, text: str) -> list[str]:
        return sorted({term for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())})


_default_indexer: MemoryIndexer | None = None


def default_memory_indexer() -> MemoryIndexer:
    """Return a lazily created memory indexer."""
    global _default_indexer
    if _default_indexer is None:
        _default_indexer = MemoryIndexer()
    return _default_indexer


def index_memory(memory: RepositoryMemory) -> dict[str, Any]:
    """Index repository memory using the default indexer."""
    return default_memory_indexer().index_memory(memory)


def build_relationship_graph(memory: RepositoryMemory) -> dict[str, list[dict[str, Any]]]:
    """Build a relationship graph using the default indexer."""
    return default_memory_indexer().build_relationship_graph(memory)
