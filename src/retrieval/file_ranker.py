"""Deterministic file ranking for repository retrieval."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from typing import Any

from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry
from src.repository.repository_state import RepositoryState
from src.retrieval.retrieval_models import RankedFile

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "does", "for",
    "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "our", "please", "should", "that", "the", "this", "to", "what", "when",
    "where", "why", "with", "you", "which", "who", "whom",
}

QUERY_EXPANSIONS = {
    "auth": {"authentication", "authorize", "authorization", "login", "token", "jwt", "session"},
    "authentication": {"auth", "authorize", "authorization", "login", "token", "jwt", "session"},
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
    "event": {"handler", "slack", "message", "route"},
    "events": {"event", "handler", "slack", "message", "route"},
    "git": {"commit", "diff", "status", "branch"},
    "error": {"exception", "bug", "fail", "failing", "failed"},
    "failing": {"error", "exception", "bug", "failed"},
    "failed": {"error", "exception", "bug", "failing"},
    "circular": {"import", "dependency", "cycle"},
}


def query_terms(query: str) -> set[str]:
    """Tokenize and expand a query into deterministic repository search terms."""
    normalized = _searchable_text(query)
    raw_terms = {
        term
        for term in normalized.split()
        if len(term) >= 2 and term not in STOPWORDS
    }
    terms = set(raw_terms)
    for term in list(terms):
        terms.update(QUERY_EXPANSIONS.get(term, set()))
    return terms


class FileRanker:
    """Rank repository files according to query, dependency, and state signals."""

    def __init__(self, content_sample_chars: int = 6_000) -> None:
        self.content_sample_chars = content_sample_chars

    def rank_files(
        self,
        user_query: str,
        repository_index: dict[str, FileIndexEntry],
        dependency_mapper: DependencyMapper | None = None,
        repository_state: RepositoryState | None = None,
        limit: int | None = None,
    ) -> list[RankedFile]:
        """Return repository files ranked by deterministic relevance score."""
        terms = query_terms(user_query)
        ranked = [
            self.score_file(
                user_query=user_query,
                path=path,
                entry=entry,
                repository_index=repository_index,
                dependency_mapper=dependency_mapper,
                repository_state=repository_state,
                terms=terms,
            )
            for path, entry in repository_index.items()
        ]
        ranked = [file for file in ranked if file.score > 0]
        ranked.sort(key=lambda file: (-file.score, file.path))
        return ranked[:limit] if limit is not None else ranked

    def score_file(
        self,
        user_query: str,
        path: str,
        entry: FileIndexEntry,
        repository_index: dict[str, FileIndexEntry] | None = None,
        dependency_mapper: DependencyMapper | None = None,
        repository_state: RepositoryState | None = None,
        terms: set[str] | None = None,
    ) -> RankedFile:
        """Score one repository file and include explainable ranking reasons."""
        terms = terms if terms is not None else query_terms(user_query)
        query_text = user_query.lower()
        basename = os.path.basename(path).lower()
        path_text = _searchable_text(path)
        basename_text = _searchable_text(basename)
        score = 0
        components: dict[str, int] = defaultdict(int)
        reasons: list[str] = []

        if path.lower() in query_text or basename in query_text:
            score += 16
            components["explicit_file_reference"] += 16
            reasons.append("mentioned-file")

        for term in sorted(terms):
            if term in basename_text:
                score += 12
                components["filename"] += 12
                reasons.append(f"filename:{term}")
            elif term in path_text:
                score += 8
                components["path"] += 8
                reasons.append(f"path:{term}")

        symbol_text = self._symbol_text(entry)
        for term in sorted(terms):
            if term in symbol_text:
                score += 7
                components["symbols"] += 7
                reasons.append(f"symbol:{term}")

        import_text = self._import_text(entry)
        for term in sorted(terms):
            if term in import_text:
                score += 4
                components["imports"] += 4
                reasons.append(f"import:{term}")

        content_sample = entry.get("content", "")[: self.content_sample_chars].lower()
        for term in sorted(terms):
            if term in content_sample:
                score += 1
                components["content"] += 1
                reasons.append(f"content:{term}")

        dependency_score, dependency_reasons = self._score_dependency_proximity(
            terms=terms,
            path=path,
            repository_index=repository_index or {},
            dependency_mapper=dependency_mapper,
        )
        score += dependency_score
        components["dependency_proximity"] += dependency_score
        reasons.extend(dependency_reasons)

        activity_score, activity_reasons = self._score_repository_activity(path, repository_state)
        score += activity_score
        components["repository_activity"] += activity_score
        reasons.extend(activity_reasons)

        dependencies = dependency_mapper.get_dependencies(path) if dependency_mapper else []
        dependents = dependency_mapper.get_dependents(path) if dependency_mapper else []
        return RankedFile(
            path=path,
            score=score,
            reasons=sorted(set(reasons)),
            source_metadata={
                "extension": entry.get("extension", ""),
                "size": entry.get("size", 0),
                "truncated": bool(entry.get("truncated", False)),
                "score_components": dict(sorted(components.items())),
            },
            dependencies=dependencies,
            dependents=dependents,
        )

    def _score_dependency_proximity(
        self,
        terms: set[str],
        path: str,
        repository_index: dict[str, FileIndexEntry],
        dependency_mapper: DependencyMapper | None,
    ) -> tuple[int, list[str]]:
        """Score a file based on relevant immediate dependencies and dependents."""
        if dependency_mapper is None:
            return 0, []

        score = 0
        reasons = []
        for relation, related_paths, weight in (
            ("dependency", dependency_mapper.get_dependencies(path), 3),
            ("dependent", dependency_mapper.get_dependents(path), 2),
        ):
            for related_path in related_paths[:4]:
                related_entry = repository_index.get(related_path)
                related_text = _searchable_text(related_path)
                if related_entry is not None:
                    related_text += " " + self._symbol_text(related_entry)
                for term in sorted(terms):
                    if term in related_text:
                        score += weight
                        reasons.append(f"{relation}:{term}")
                        break
        return score, reasons

    def _score_repository_activity(
        self,
        path: str,
        repository_state: RepositoryState | None,
    ) -> tuple[int, list[str]]:
        """Score active working-tree files as recent repository activity."""
        if repository_state is None:
            return 0, []

        score = 0
        reasons = []
        if path in repository_state.changed_files:
            score += 14
            reasons.append("git:changed")
        if path in repository_state.staged_files:
            score += 12
            reasons.append("git:staged")
        if path in repository_state.untracked_files:
            score += 10
            reasons.append("git:untracked")
        return score, reasons

    def _symbol_text(self, entry: FileIndexEntry) -> str:
        """Return searchable symbol text for one file."""
        chunks: list[str] = []
        for function in entry["symbols"]["functions"]:
            chunks.extend([function.get("name", ""), function.get("docstring") or ""])
            chunks.extend(function.get("arguments", []))
        for class_info in entry["symbols"]["classes"]:
            chunks.extend([class_info.get("name", ""), class_info.get("docstring") or ""])
            chunks.extend(class_info.get("arguments", []))
            for method in class_info.get("methods", []):
                chunks.extend([method.get("name", ""), method.get("docstring") or ""])
                chunks.extend(method.get("arguments", []))
        return _searchable_text(" ".join(chunks))

    def _import_text(self, entry: FileIndexEntry) -> str:
        """Return searchable import text for one file."""
        return _searchable_text(
            " ".join(
                f"{item.get('module', '')} {item.get('name', '')} {item.get('alias') or ''}"
                for item in entry["symbols"]["imports"]
            )
        )


def _searchable_text(value: Any) -> str:
    """Return a token-friendly lowercase text representation."""
    text = str(value)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[^A-Za-z0-9_]+", " ", text)
    return text.lower()
