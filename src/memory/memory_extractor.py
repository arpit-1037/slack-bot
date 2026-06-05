"""Extract reusable repository facts from existing repository intelligence."""

from __future__ import annotations

import os
import re
from typing import Any

from src.memory.memory_models import (
    MemoryRelationship,
    RepositoryFact,
    RepositoryMemory,
    stable_memory_id,
)
from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.repository.repository_state import RepositoryState
from src.utils.helpers import get_logger

log = get_logger(__name__)


class MemoryExtractor:
    """Extract repository-only facts from scanner, symbol, dependency, and git state."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()

    def extract_repository_facts(
        self,
        project_path: str,
        request_id: str | None = None,
    ) -> RepositoryMemory:
        """Extract reusable facts for a repository without storing user data."""
        project_path = os.path.abspath(os.path.expanduser(project_path))
        index = self.indexer.ensure_index(project_path)
        state = self.indexer.repository_state or self.indexer.get_repository_state(project_path)
        self.dependency_mapper.refresh(index, repository_state=state)

        facts: list[RepositoryFact] = []
        relationships: list[MemoryRelationship] = []
        facts.extend(self._architecture_facts(project_path, index, state))
        facts.extend(self._file_and_symbol_facts(project_path, index, state))
        relationships.extend(self._dependency_relationships(index))

        memory = RepositoryMemory(
            repo_path=project_path,
            repo_id=stable_memory_id(project_path),
            facts=facts,
            relationships=relationships,
            metadata={
                "branch": state.branch,
                "head_commit": state.head_commit,
                "file_count": state.file_count,
                "source": "memory_extractor",
            },
        )
        log.info(
            "request_id=%s repository_memory extracted repo=%s facts=%d relationships=%d",
            request_id,
            project_path,
            len(facts),
            len(relationships),
        )
        return memory

    def _architecture_facts(
        self,
        repo_path: str,
        index: dict[str, FileIndexEntry],
        state: RepositoryState,
    ) -> list[RepositoryFact]:
        facts: list[RepositoryFact] = []
        if "app.py" in index:
            facts.append(
                self._fact(
                    repo_path,
                    "entry_point",
                    "application entry point",
                    "app.py",
                    0.99,
                    file_path="app.py",
                    tags=["entry", "entrypoint", "app", "flask"],
                    source="repository_indexer",
                    state=state,
                )
            )

        for path, entry in sorted(index.items()):
            lowered_path = path.lower()
            content = entry.get("content", "")
            lowered_content = content.lower()
            if "from flask" in lowered_content or "import flask" in lowered_content:
                facts.append(
                    self._fact(
                        repo_path,
                        "framework",
                        "framework",
                        "Flask",
                        0.99,
                        file_path=path,
                        tags=["framework", "flask", "web"],
                        source="repository_scanner",
                        state=state,
                    )
                )
            if "sqlite3" in lowered_content or "tasks.db" in lowered_content:
                facts.append(
                    self._fact(
                        repo_path,
                        "database",
                        "database",
                        "SQLite",
                        0.96,
                        file_path=path,
                        tags=["database", "sqlite", "tasks"],
                        source="repository_scanner",
                        state=state,
                    )
                )
            if "chromadb" in lowered_content or "chroma" in lowered_path:
                facts.append(
                    self._fact(
                        repo_path,
                        "vector_store",
                        "vector store",
                        "ChromaDB",
                        0.94,
                        file_path=path,
                        tags=["vector", "semantic", "embedding", "chromadb"],
                        source="repository_scanner",
                        state=state,
                    )
                )
            if "slack_handler" in lowered_path:
                facts.append(
                    self._fact(
                        repo_path,
                        "architecture",
                        "Slack event processing start",
                        path,
                        0.98,
                        file_path=path,
                        tags=["slack", "event", "events", "processing", "handler", "start"],
                        source="repository_indexer",
                        state=state,
                    )
                )
            if lowered_path.startswith("src/tools/git") or lowered_path == "src/tools/git_tool.py":
                facts.append(
                    self._fact(
                        repo_path,
                        "architecture",
                        "git logic",
                        path,
                        0.96,
                        file_path=path,
                        tags=["git", "tools", "logic", "module"],
                        source="repository_indexer",
                        state=state,
                    )
                )
            if lowered_path.startswith("src/planning") or lowered_path.startswith("src/planner"):
                facts.append(
                    self._fact(
                        repo_path,
                        "architecture",
                        "planning system",
                        path,
                        0.94,
                        file_path=path,
                        tags=["planning", "planner", "plan", "engine"],
                        source="repository_indexer",
                        state=state,
                    )
                )
            if lowered_path.startswith("src/execution"):
                facts.append(
                    self._fact(
                        repo_path,
                        "architecture",
                        "execution engine",
                        path,
                        0.94,
                        file_path=path,
                        tags=["execution", "engine", "investigation", "read-only"],
                        source="repository_indexer",
                        state=state,
                    )
                )
            if lowered_path == "src/tools/tool_executor.py":
                facts.append(
                    self._fact(
                        repo_path,
                        "architecture",
                        "tool executor",
                        path,
                        0.96,
                        file_path=path,
                        tags=["tool", "executor", "tools"],
                        source="repository_indexer",
                        state=state,
                    )
                )
        return facts

    def _file_and_symbol_facts(
        self,
        repo_path: str,
        index: dict[str, FileIndexEntry],
        state: RepositoryState,
    ) -> list[RepositoryFact]:
        facts: list[RepositoryFact] = []
        for path, entry in sorted(index.items()):
            tags = self._path_tags(path)
            facts.append(
                self._fact(
                    repo_path,
                    "module",
                    f"module {path}",
                    path,
                    0.74,
                    file_path=path,
                    tags=tags,
                    source="repository_indexer",
                    state=state,
                    metadata={"extension": entry.get("extension", ""), "size": entry.get("size", 0)},
                )
            )
            symbols = entry.get("symbols", {})
            for function in symbols.get("functions", []):
                facts.append(self._symbol_fact(repo_path, state, path, "function", function))
            for class_info in symbols.get("classes", []):
                facts.append(self._symbol_fact(repo_path, state, path, "class", class_info))
                for method in class_info.get("methods", []):
                    facts.append(self._symbol_fact(repo_path, state, path, "method", method))
        return facts

    def _dependency_relationships(
        self,
        index: dict[str, FileIndexEntry],
    ) -> list[MemoryRelationship]:
        relationships: list[MemoryRelationship] = []
        for source in sorted(index):
            for target in self.dependency_mapper.get_dependencies(source):
                relationships.append(
                    MemoryRelationship(
                        id=stable_memory_id(source, "uses", target),
                        source=source,
                        target=target,
                        relationship_type="uses",
                        confidence=0.82,
                        source_path=source,
                        target_path=target,
                        evidence=[f"{source} imports {target}"],
                    )
                )
        return relationships

    def _symbol_fact(
        self,
        repo_path: str,
        state: RepositoryState,
        path: str,
        kind: str,
        symbol: dict[str, Any],
    ) -> RepositoryFact:
        name = str(symbol.get("name") or "")
        return self._fact(
            repo_path,
            "symbol",
            f"{kind} {name}",
            path,
            0.87,
            file_path=path,
            symbol_name=name,
            tags=self._path_tags(path) + [kind, name.lower()],
            source="symbol_extractor",
            state=state,
            metadata={
                "kind": kind,
                "line_start": symbol.get("line_start"),
                "line_end": symbol.get("line_end"),
            },
        )

    def _fact(
        self,
        repo_path: str,
        fact_type: str,
        key: str,
        value: str,
        confidence: float,
        file_path: str = "",
        symbol_name: str = "",
        tags: list[str] | None = None,
        source: str = "memory_extractor",
        state: RepositoryState | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RepositoryFact:
        return RepositoryFact(
            id=stable_memory_id(repo_path, fact_type, key, value, file_path, symbol_name),
            fact_type=fact_type,  # type: ignore[arg-type]
            key=key,
            value=value,
            confidence=confidence,
            repo_path=repo_path,
            file_path=file_path,
            symbol_name=symbol_name,
            source=source,
            evidence=[file_path] if file_path else [],
            tags=sorted(set(tags or [])),
            branch=state.branch if state else "",
            head_commit=state.head_commit if state else "",
            metadata=dict(metadata or {}),
        )

    def _path_tags(self, path: str) -> list[str]:
        parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", path.lower())
        tags = set(parts)
        for part in path.lower().replace("\\", "/").split("/"):
            if part:
                tags.add(part.removesuffix(".py"))
        return sorted(tags)


_default_extractor: MemoryExtractor | None = None


def default_memory_extractor() -> MemoryExtractor:
    """Return a lazily created memory extractor."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = MemoryExtractor()
    return _default_extractor


def extract_repository_facts(project_path: str, request_id: str | None = None) -> RepositoryMemory:
    """Extract repository facts using the default extractor."""
    return default_memory_extractor().extract_repository_facts(project_path, request_id=request_id)
