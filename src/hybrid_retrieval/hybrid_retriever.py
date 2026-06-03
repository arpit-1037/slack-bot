"""Main orchestrator for hybrid repository retrieval."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from typing import Mapping

from src.embeddings.embedding_models import EmbeddingSearchResponse, SearchResult
from src.embeddings.index_builder import EmbeddingIndexBuilder
from src.hybrid_retrieval.context_optimizer import ContextOptimizer
from src.hybrid_retrieval.retrieval_models import (
    SIGNAL_DEPENDENCY,
    SIGNAL_GIT,
    SIGNAL_KEYWORD,
    SIGNAL_SEMANTIC,
    HybridRetrievalResult,
    RankedCandidate,
    RetrievalSignal,
)
from src.hybrid_retrieval.retrieval_ranker import RetrievalRanker
from src.hybrid_retrieval.score_fusion import DEFAULT_SIGNAL_WEIGHTS, ScoreFusion
from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.repository.repository_state import RepositoryState
from src.retrieval.context_assembler import ContextAssembler
from src.retrieval.file_ranker import FileRanker, query_terms
from src.retrieval.retrieval_models import RankedFile
from src.retrieval.symbol_ranker import SymbolRanker
from src.utils.helpers import bool_env, get_logger, int_env

log = get_logger(__name__)


def calculate_git_relevance(
    file_path: str,
    repository_state: RepositoryState | None,
    recent_commit_files: list[str] | None = None,
) -> int:
    """Calculate bounded git relevance from current state and recent history."""
    if repository_state is None:
        return 0

    score = 0
    if file_path in repository_state.staged_files:
        score = max(score, 90)
    if file_path in repository_state.changed_files:
        score = max(score, 85)
    if file_path in repository_state.untracked_files:
        score = max(score, 70)

    for index, recent_path in enumerate(recent_commit_files or []):
        if file_path != recent_path:
            continue
        score = max(score, max(35, 65 - index))
        break
    return score


class HybridRetriever:
    """Coordinate keyword, dependency, semantic, and git retrieval signals."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
        file_ranker: FileRanker | None = None,
        symbol_ranker: SymbolRanker | None = None,
        context_optimizer: ContextOptimizer | None = None,
        context_assembler: ContextAssembler | None = None,
        embedding_index_builder: EmbeddingIndexBuilder | None = None,
        ranker: RetrievalRanker | None = None,
        score_fusion: ScoreFusion | None = None,
        score_weights: Mapping[str, float] | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
        dependency_limit: int | None = None,
        enable_semantic_search: bool | None = None,
        git_history_limit: int | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()
        self.file_ranker = file_ranker or FileRanker()
        self.symbol_ranker = symbol_ranker or SymbolRanker()
        self.max_files = max_files or int_env("RETRIEVAL_MAX_FILES", 6, 1)
        self.max_symbols = max_symbols or int_env("RETRIEVAL_MAX_SYMBOLS", 12, 1)
        self.dependency_limit = dependency_limit or int_env("RETRIEVAL_DEPENDENCY_LIMIT", 2, 0)
        self.git_history_limit = git_history_limit or int_env("HYBRID_RETRIEVAL_GIT_HISTORY_LIMIT", 20, 0)
        self.enable_semantic_search = (
            enable_semantic_search
            if enable_semantic_search is not None
            else bool_env("RETRIEVAL_ENABLE_SEMANTIC", False)
        )
        self.embedding_index_builder = embedding_index_builder or EmbeddingIndexBuilder(indexer=self.indexer)
        self.ranker = ranker or RetrievalRanker()
        self.score_fusion = score_fusion or ScoreFusion(score_weights or DEFAULT_SIGNAL_WEIGHTS)
        self.context_optimizer = context_optimizer or ContextOptimizer(
            context_assembler=context_assembler,
            max_files=self.max_files,
            max_symbols=self.max_symbols,
        )

    def retrieve(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
    ) -> HybridRetrievalResult:
        """Return a full hybrid retrieval result for a repository query."""
        project_path = os.path.abspath(os.path.expanduser(project_path))
        file_limit = max_files or self.max_files
        symbol_limit = max_symbols or self.max_symbols
        repository_index, repository_state = self._prepare_repository(project_path)
        candidates = self.retrieve_candidates(
            project_path=project_path,
            query=query,
            request_id=request_id,
            max_files=file_limit,
            repository_index=repository_index,
            repository_state=repository_state,
        )
        fused = self.score_fusion.fuse_scores(candidates)
        ranked_candidates = self.ranker.rank_candidates(fused)
        ranked_files = self.ranker.rank_files(
            ranked_candidates,
            repository_index=repository_index,
            dependency_mapper=self.dependency_mapper,
            limit=file_limit,
        )
        if not ranked_files:
            ranked_files = self._fallback_files(repository_index, repository_state, file_limit)

        ranked_symbols = self.symbol_ranker.rank_symbols(
            user_query=query,
            repository_index=repository_index,
            ranked_files=ranked_files,
            dependency_mapper=self.dependency_mapper,
            repository_state=repository_state,
            limit=symbol_limit,
        )
        ranked_symbols = self.ranker.rank_symbols(ranked_symbols, limit=symbol_limit)
        optimized_context = self.context_optimizer.optimize_context(
            query=query,
            repository_index=repository_index,
            ranked_files=ranked_files,
            ranked_symbols=ranked_symbols,
            repository_state=repository_state,
            max_files=file_limit,
            max_symbols=symbol_limit,
        )
        explanations = self.explain_retrieval(ranked_candidates[:file_limit])
        log.info(
            "request_id=%s hybrid retrieval files=%d symbols=%d candidates=%d semantic=%s",
            request_id,
            len(ranked_files),
            len(ranked_symbols),
            len(ranked_candidates),
            self.enable_semantic_search,
        )
        return HybridRetrievalResult(
            query=query,
            terms=sorted(query_terms(query)),
            candidates=ranked_candidates,
            files=optimized_context.files,
            symbols=optimized_context.symbols,
            optimized_context=optimized_context,
            explanations=explanations,
            metadata={
                "request_id": request_id,
                "semantic_enabled": self.enable_semantic_search,
                "score_weights": self.score_fusion.weights,
            },
        )

    def retrieve_context(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
    ) -> HybridRetrievalResult:
        """Return hybrid retrieval context for a repository query."""
        return self.retrieve(
            project_path=project_path,
            query=query,
            request_id=request_id,
            max_files=max_files,
            max_symbols=max_symbols,
        )

    def retrieve_candidates(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
        max_files: int | None = None,
        repository_index: dict[str, FileIndexEntry] | None = None,
        repository_state: RepositoryState | None = None,
    ) -> list[RankedCandidate]:
        """Collect raw retrieval candidates before score fusion."""
        repository_index, repository_state = (
            (repository_index, repository_state)
            if repository_index is not None and repository_state is not None
            else self._prepare_repository(project_path)
        )
        file_limit = max_files or self.max_files
        candidates: dict[str, RankedCandidate] = {}
        keyword_ranked = self._keyword_and_inline_signals(query, repository_index, repository_state, candidates)
        self._dependency_signals(keyword_ranked, repository_index, candidates, file_limit=file_limit)
        self._semantic_signals(project_path, query, repository_index, candidates, request_id, file_limit=file_limit)
        self._git_signals(project_path, repository_index, repository_state, candidates)

        if not candidates:
            self._fallback_candidate_signals(repository_index, repository_state, candidates, limit=file_limit)
        return list(candidates.values())

    def explain_ranking(self, candidate: RankedCandidate) -> str:
        """Return a compact explanation for one hybrid-ranked candidate."""
        return self.ranker.explain_ranking(candidate)

    def explain_retrieval(self, candidates: list[RankedCandidate]) -> list[str]:
        """Return human-readable explanations for selected candidates."""
        return [self.explain_ranking(candidate) for candidate in candidates]

    def calculate_git_relevance(
        self,
        file_path: str,
        repository_state: RepositoryState | None,
        recent_commit_files: list[str] | None = None,
    ) -> int:
        """Calculate git relevance for a file."""
        return calculate_git_relevance(file_path, repository_state, recent_commit_files)

    def _prepare_repository(self, project_path: str) -> tuple[dict[str, FileIndexEntry], RepositoryState]:
        """Ensure repository index and dependency graph are current."""
        repository_index = self.indexer.ensure_index(project_path)
        repository_state = self.indexer.repository_state or self.indexer.get_repository_state(project_path)
        self.dependency_mapper.refresh(repository_index, repository_state=repository_state)
        return repository_index, repository_state

    def _keyword_and_inline_signals(
        self,
        query: str,
        repository_index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
        candidates: dict[str, RankedCandidate],
    ) -> list[RankedFile]:
        """Collect keyword, dependency-proximity, and working-tree signals from file ranking."""
        ranked_files = self.file_ranker.rank_files(
            user_query=query,
            repository_index=repository_index,
            dependency_mapper=self.dependency_mapper,
            repository_state=repository_state,
        )
        for ranked_file in ranked_files:
            components = ranked_file.source_metadata.get("score_components", {})
            keyword_score = self._keyword_score(components)
            if keyword_score > 0:
                self._add_signal(
                    candidates,
                    file_path=ranked_file.path,
                    signal=RetrievalSignal(
                        source=SIGNAL_KEYWORD,
                        raw_score=float(keyword_score),
                        reason=self._compact_reason(ranked_file.reasons, fallback="keyword-match"),
                        file_path=ranked_file.path,
                        metadata={"components": components, "legacy_score": ranked_file.score},
                    ),
                )

            dependency_score = float(components.get("dependency_proximity", 0) or 0)
            if dependency_score > 0:
                self._add_signal(
                    candidates,
                    file_path=ranked_file.path,
                    signal=RetrievalSignal(
                        source=SIGNAL_DEPENDENCY,
                        raw_score=dependency_score,
                        reason="dependency-proximity",
                        file_path=ranked_file.path,
                        metadata={"components": components},
                    ),
                )

            git_score = float(components.get("repository_activity", 0) or 0)
            if git_score > 0:
                self._add_signal(
                    candidates,
                    file_path=ranked_file.path,
                    signal=RetrievalSignal(
                        source=SIGNAL_GIT,
                        raw_score=git_score,
                        reason="git-working-tree",
                        file_path=ranked_file.path,
                        metadata={"components": components},
                    ),
                )
        return ranked_files

    def _dependency_signals(
        self,
        ranked_files: list[RankedFile],
        repository_index: dict[str, FileIndexEntry],
        candidates: dict[str, RankedCandidate],
        file_limit: int,
    ) -> None:
        """Add dependency and dependent candidates around relevant files."""
        source_limit = max(file_limit * 2, 6)
        for ranked_file in ranked_files[:source_limit]:
            source_score = self._ranked_file_source_score(ranked_file)
            for related_path, relation, penalty in self._related_paths(ranked_file):
                if related_path not in repository_index:
                    continue
                self._add_signal(
                    candidates,
                    file_path=related_path,
                    signal=RetrievalSignal(
                        source=SIGNAL_DEPENDENCY,
                        raw_score=max(source_score - penalty, 1.0),
                        reason=f"{relation}:{ranked_file.path}",
                        file_path=related_path,
                        metadata={"source_file": ranked_file.path, "source_score": ranked_file.score},
                    ),
                )

    def _semantic_signals(
        self,
        project_path: str,
        query: str,
        repository_index: dict[str, FileIndexEntry],
        candidates: dict[str, RankedCandidate],
        request_id: str | None,
        file_limit: int,
    ) -> None:
        """Add semantic vector-search candidates when enabled."""
        if not self.enable_semantic_search:
            return
        try:
            response = self.embedding_index_builder.semantic_search(
                project_path=project_path,
                query=query,
                limit=max(file_limit * 3, 8),
                request_id=request_id,
            )
        except Exception as error:
            log.warning("request_id=%s hybrid semantic retrieval skipped: %s", request_id, error)
            return

        for result in self._best_semantic_results(response):
            file_path = result.chunk.file_path
            if file_path not in repository_index:
                continue
            reason = f"semantic:{result.similarity_score:.2f}"
            if result.chunk.symbol_name:
                reason += f":{result.chunk.symbol_name}"
            self._add_signal(
                candidates,
                file_path=file_path,
                signal=RetrievalSignal(
                    source=SIGNAL_SEMANTIC,
                    raw_score=max(float(result.similarity_score), 0.0),
                    reason=reason,
                    file_path=file_path,
                    symbol_name=result.chunk.symbol_name,
                    metadata={
                        "chunk_id": result.chunk.chunk_id,
                        "chunk_type": result.chunk.chunk_type,
                        "line_start": result.chunk.line_start,
                        "line_end": result.chunk.line_end,
                        "backend": response.metadata.get("backend"),
                        "provider": response.metadata.get("provider"),
                    },
                ),
            )

    def _git_signals(
        self,
        project_path: str,
        repository_index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
        candidates: dict[str, RankedCandidate],
    ) -> None:
        """Add bounded git working-tree and recent-history candidates."""
        recent_commit_files = self._recent_commit_paths(project_path, repository_state)
        active_paths = set(repository_state.changed_files + repository_state.staged_files + repository_state.untracked_files)
        active_paths.update(recent_commit_files)
        for path in sorted(active_paths):
            if path not in repository_index:
                continue
            score = self.calculate_git_relevance(path, repository_state, recent_commit_files)
            if score <= 0:
                continue
            self._add_signal(
                candidates,
                file_path=path,
                signal=RetrievalSignal(
                    source=SIGNAL_GIT,
                    raw_score=float(score),
                    reason=self._git_reason(path, repository_state, recent_commit_files),
                    file_path=path,
                    metadata={
                        "branch": repository_state.branch,
                        "head_commit": repository_state.head_commit,
                    },
                ),
            )

    def _fallback_candidate_signals(
        self,
        repository_index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
        candidates: dict[str, RankedCandidate],
        limit: int,
    ) -> None:
        """Add stable overview candidates when no retrieval signal matches."""
        for file in self._fallback_files(repository_index, repository_state, limit):
            self._add_signal(
                candidates,
                file_path=file.path,
                signal=RetrievalSignal(
                    source=SIGNAL_KEYWORD,
                    raw_score=1.0,
                    reason=file.reasons[0] if file.reasons else "fallback",
                    file_path=file.path,
                ),
            )

    def _fallback_files(
        self,
        repository_index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
        limit: int,
    ) -> list[RankedFile]:
        """Choose stable overview or active files when no signal matches."""
        preferred_names = {"README.md", "app.py", "main.py", "index.js", "package.json"}
        active = repository_state.changed_files + repository_state.staged_files + repository_state.untracked_files
        candidates = [path for path in active if path in repository_index]
        candidates.extend(
            path for path in sorted(repository_index)
            if os.path.basename(path) in preferred_names and path not in candidates
        )
        candidates.extend(path for path in sorted(repository_index) if path not in candidates)
        return [
            RankedFile(
                path=path,
                score=1,
                reasons=["fallback:active-file" if path in active else "fallback:overview"],
                source_metadata={
                    "extension": repository_index[path].get("extension", ""),
                    "size": repository_index[path].get("size", 0),
                    "truncated": bool(repository_index[path].get("truncated", False)),
                },
                dependencies=self.dependency_mapper.get_dependencies(path),
                dependents=self.dependency_mapper.get_dependents(path),
            )
            for path in candidates[:limit]
        ]

    def _add_signal(
        self,
        candidates: dict[str, RankedCandidate],
        file_path: str,
        signal: RetrievalSignal,
        candidate_type: str = "file",
        symbol_name: str | None = None,
    ) -> None:
        """Add a retrieval signal to the candidate map."""
        candidate_id = f"{candidate_type}:{file_path}:{symbol_name or ''}"
        candidate = candidates.get(candidate_id)
        if candidate is None:
            candidate = RankedCandidate(
                candidate_id=candidate_id,
                candidate_type=candidate_type,
                file_path=file_path,
                symbol_name=symbol_name,
            )
        candidates[candidate_id] = replace(
            candidate,
            signals=[*candidate.signals, signal],
            retrieval_systems=sorted({*candidate.retrieval_systems, signal.source}),
        )

    def _related_paths(self, file: RankedFile) -> list[tuple[str, str, float]]:
        """Return bounded dependency and dependent paths for one ranked file."""
        related = []
        for dependency in file.dependencies[: self.dependency_limit]:
            related.append((dependency, "dependency-of", 8.0))
        for dependent in file.dependents[: self.dependency_limit]:
            related.append((dependent, "dependent-of", 10.0))
        return related

    def _best_semantic_results(self, response: EmbeddingSearchResponse) -> list[SearchResult]:
        """Return the best semantic chunk per file in response order."""
        best_by_path: dict[str, SearchResult] = {}
        for result in response.results:
            current = best_by_path.get(result.chunk.file_path)
            if current is None or result.similarity_score > current.similarity_score:
                best_by_path[result.chunk.file_path] = result
        return sorted(best_by_path.values(), key=lambda item: (-item.similarity_score, item.chunk.file_path))

    def _recent_commit_paths(self, project_path: str, repository_state: RepositoryState) -> list[str]:
        """Return ordered files touched by recent commits using read-only git commands."""
        if self.git_history_limit <= 0 or not repository_state.health.is_git_repo:
            return []
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            result = subprocess.run(
                ["git", "log", "--name-only", "--pretty=format:", f"--max-count={self.git_history_limit}"],
                cwd=project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
                env=env,
            )
        except Exception as error:
            log.debug("Recent git history lookup failed path=%s error=%s", project_path, error)
            return []
        if result.returncode != 0:
            return []
        paths = []
        seen = set()
        for line in result.stdout.splitlines():
            path = line.strip().replace(os.sep, "/")
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def _keyword_score(self, components: Mapping[str, object]) -> float:
        """Return keyword score with dependency and git components excluded."""
        excluded = {"dependency_proximity", "repository_activity"}
        score = 0.0
        for name, value in components.items():
            if name in excluded:
                continue
            try:
                score += float(value)
            except (TypeError, ValueError):
                continue
        return score

    def _ranked_file_source_score(self, ranked_file: RankedFile) -> float:
        """Return a useful raw score for dependency expansion from a ranked file."""
        components = ranked_file.source_metadata.get("score_components", {})
        keyword_score = self._keyword_score(components)
        return max(keyword_score, float(ranked_file.score))

    def _compact_reason(self, reasons: list[str], fallback: str) -> str:
        """Return the first non-git dependency-neutral reason for a candidate."""
        for reason in reasons:
            if reason.startswith(("git:", "dependency:", "dependent:")):
                continue
            return reason
        return fallback

    def _git_reason(
        self,
        path: str,
        repository_state: RepositoryState,
        recent_commit_files: list[str],
    ) -> str:
        """Return the strongest git reason for a file."""
        if path in repository_state.staged_files:
            return "git:staged"
        if path in repository_state.changed_files:
            return "git:changed"
        if path in repository_state.untracked_files:
            return "git:untracked"
        if path in recent_commit_files:
            return "git:recent-commit"
        return "git:repository-state"
