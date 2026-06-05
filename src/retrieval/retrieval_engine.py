"""Repository retrieval orchestration over deterministic rankers."""

from __future__ import annotations

import os

from src.embeddings.index_builder import EmbeddingIndexBuilder
from src.embeddings.embedding_models import SearchResult
from src.hybrid_retrieval.context_optimizer import ContextOptimizer
from src.hybrid_retrieval.hybrid_retriever import HybridRetriever
from src.memory.memory_models import MemoryResult
from src.memory.repository_memory import RepositoryMemory
from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.repository.repository_state import RepositoryState
from src.retrieval.context_assembler import ContextAssembler
from src.retrieval.file_ranker import FileRanker, query_terms
from src.retrieval.retrieval_models import RankedFile, RankedSymbol, RetrievalContext, RetrievalResult
from src.retrieval.symbol_ranker import SymbolRanker
from src.utils.helpers import bool_env, get_logger, int_env

log = get_logger(__name__)


class RepositoryRetrievalEngine:
    """Coordinate file ranking, symbol ranking, dependency expansion, and context assembly."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
        file_ranker: FileRanker | None = None,
        symbol_ranker: SymbolRanker | None = None,
        context_assembler: ContextAssembler | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
        dependency_limit: int | None = None,
        embedding_index_builder: EmbeddingIndexBuilder | None = None,
        enable_semantic_search: bool | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        repository_memory: RepositoryMemory | None = None,
        enable_repository_memory: bool | None = None,
        repository_memory_confidence: float | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()
        self.file_ranker = file_ranker or FileRanker()
        self.symbol_ranker = symbol_ranker or SymbolRanker()
        self.context_assembler = context_assembler or ContextAssembler()
        self.max_files = max_files or int_env("RETRIEVAL_MAX_FILES", 6, 1)
        self.max_symbols = max_symbols or int_env("RETRIEVAL_MAX_SYMBOLS", 12, 1)
        self.dependency_limit = dependency_limit or int_env("RETRIEVAL_DEPENDENCY_LIMIT", 2, 0)
        self.embedding_index_builder = embedding_index_builder or EmbeddingIndexBuilder(indexer=self.indexer)
        self.enable_semantic_search = (
            enable_semantic_search
            if enable_semantic_search is not None
            else bool_env("RETRIEVAL_ENABLE_SEMANTIC", False)
        )
        self.repository_memory = repository_memory
        self.enable_repository_memory = (
            enable_repository_memory
            if enable_repository_memory is not None
            else bool_env("REPOSITORY_MEMORY_ENABLE", True)
        )
        self.repository_memory_confidence = (
            repository_memory_confidence
            if repository_memory_confidence is not None
            else self._float_env("REPOSITORY_MEMORY_CONFIDENCE_THRESHOLD", 0.9)
        )
        self.hybrid_retriever = hybrid_retriever or HybridRetriever(
            indexer=self.indexer,
            dependency_mapper=self.dependency_mapper,
            file_ranker=self.file_ranker,
            symbol_ranker=self.symbol_ranker,
            context_optimizer=ContextOptimizer(
                context_assembler=self.context_assembler,
                max_files=self.max_files,
                max_symbols=self.max_symbols,
            ),
            embedding_index_builder=self.embedding_index_builder,
            max_files=self.max_files,
            max_symbols=self.max_symbols,
            dependency_limit=self.dependency_limit,
            enable_semantic_search=self.enable_semantic_search,
        )

    def retrieve_context(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
        max_files: int | None = None,
        max_symbols: int | None = None,
    ) -> RetrievalResult:
        """Return focused repository context for a user query."""
        memory_result = self._retrieve_from_memory(project_path, query, request_id=request_id)
        if memory_result is not None and memory_result.hit:
            log.info(
                "request_id=%s retrieval repository_memory_hit confidence=%.4f query=%r",
                request_id,
                memory_result.best_confidence,
                query,
            )
            return self._memory_result_to_retrieval_result(query, memory_result)

        file_limit = max_files or self.max_files
        symbol_limit = max_symbols or self.max_symbols
        hybrid_result = self.hybrid_retriever.retrieve(
            project_path=project_path,
            query=query,
            request_id=request_id,
            max_files=file_limit,
            max_symbols=symbol_limit,
        )
        log.info(
            "request_id=%s retrieval context files=%d symbols=%d snippets=%d terms=%s",
            request_id,
            len(hybrid_result.files),
            len(hybrid_result.symbols),
            len(hybrid_result.context.snippets),
            ",".join(hybrid_result.terms),
        )
        return hybrid_result.to_retrieval_result()

    def _retrieve_from_memory(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
    ) -> MemoryResult | None:
        if not self.enable_repository_memory:
            return None
        try:
            memory = self.repository_memory or RepositoryMemory(project_path)
            result = memory.retrieve_memory(
                query,
                min_confidence=self.repository_memory_confidence,
                max_results=max(self.max_files, self.max_symbols),
            )
        except Exception as error:
            log.warning("request_id=%s repository memory retrieval skipped: %s", request_id, error)
            return None
        return result

    def _memory_result_to_retrieval_result(
        self,
        query: str,
        result: MemoryResult,
    ) -> RetrievalResult:
        files: list[RankedFile] = []
        symbols: list[RankedSymbol] = []
        seen_files: set[str] = set()
        for entry in result.entries:
            fact = entry.fact
            score = max(1, min(100, int(entry.score * 100)))
            reasons = ["repository-memory", *entry.reasons]
            if fact.file_path and fact.file_path not in seen_files:
                files.append(
                    RankedFile(
                        path=fact.file_path,
                        score=score,
                        reasons=reasons,
                        source_metadata={
                            "memory_fact_id": fact.id,
                            "memory_confidence": fact.confidence,
                            "fact_type": fact.fact_type,
                        },
                    )
                )
                seen_files.add(fact.file_path)
            if fact.symbol_name and fact.file_path:
                symbols.append(
                    RankedSymbol(
                        name=fact.symbol_name,
                        kind=str(fact.metadata.get("kind") or "symbol"),
                        file_path=fact.file_path,
                        score=score,
                        line_start=int(fact.metadata.get("line_start") or 0),
                        line_end=int(fact.metadata.get("line_end") or 0),
                        reasons=reasons,
                        source_metadata={"memory_fact_id": fact.id},
                    )
                )

        context = RetrievalContext(
            query=query,
            files=files,
            symbols=symbols,
            snippets=[],
            repository_summary={
                "memory": {
                    "hit": result.hit,
                    "best_confidence": result.best_confidence,
                    "summary": result.summary,
                }
            },
            ranking_decisions=[
                f"Repository memory hit confidence={result.best_confidence:.4f}",
                result.summary,
            ],
        )
        return RetrievalResult(
            query=query,
            terms=query_terms(query),
            files=files,
            symbols=symbols,
            context=context,
        )

    def retrieve_files(
        self,
        project_path: str,
        query: str,
        request_id: str | None = None,
        limit: int | None = None,
    ) -> list[RankedFile]:
        """Return ranked files for a user query."""
        ranked_files = self.hybrid_retriever.retrieve(
            project_path=project_path,
            query=query,
            request_id=request_id,
            max_files=limit or self.max_files,
            max_symbols=self.max_symbols,
        ).files
        log.info("request_id=%s retrieval files=%d", request_id, len(ranked_files))
        return ranked_files

    def retrieve_symbols(
        self,
        project_path: str,
        query: str,
        ranked_files: list[RankedFile] | None = None,
        request_id: str | None = None,
        limit: int | None = None,
    ) -> list[RankedSymbol]:
        """Return ranked symbols for a user query."""
        if ranked_files is None:
            symbols = self.hybrid_retriever.retrieve(
                project_path=project_path,
                query=query,
                request_id=request_id,
                max_files=self.max_files,
                max_symbols=limit or self.max_symbols,
            ).symbols
        else:
            index, repository_state = self._prepare_repository(project_path)
            symbols = self.symbol_ranker.rank_symbols(
                user_query=query,
                repository_index=index,
                ranked_files=ranked_files,
                dependency_mapper=self.dependency_mapper,
                repository_state=repository_state,
                limit=limit or self.max_symbols,
            )
        log.info("request_id=%s retrieval symbols=%d", request_id, len(symbols))
        return symbols

    def _prepare_repository(self, project_path: str) -> tuple[dict[str, FileIndexEntry], RepositoryState]:
        """Ensure repository index and dependency maps are current."""
        index = self.indexer.ensure_index(project_path)
        repository_state = self.indexer.repository_state or self.indexer.get_repository_state(project_path)
        self.dependency_mapper.refresh(index, repository_state=repository_state)
        return index, repository_state

    def _float_env(self, name: str, default: float) -> float:
        try:
            return float(os.getenv(name, default))
        except (TypeError, ValueError):
            return default

    def _rank_and_expand_files(
        self,
        query: str,
        index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
        limit: int,
    ) -> list[RankedFile]:
        """Rank files and add limited dependency-aware expansion."""
        ranked = self.file_ranker.rank_files(
            user_query=query,
            repository_index=index,
            dependency_mapper=self.dependency_mapper,
            repository_state=repository_state,
        )
        if self.enable_semantic_search:
            ranked = self._merge_semantic_results(
                ranked,
                query=query,
                index=index,
                repository_state=repository_state,
                limit=limit,
            )
        if not ranked:
            ranked = self._fallback_files(index, repository_state)

        selected: dict[str, RankedFile] = {}
        ranked_by_path = {file.path: file for file in ranked}
        for file in ranked[:limit]:
            selected[file.path] = file

        for file in ranked[: min(limit, 3)]:
            for related_path, reason, penalty in self._related_paths(file):
                if related_path not in index:
                    continue
                source = ranked_by_path.get(related_path)
                expanded = source or self._expanded_file(related_path, index[related_path], file, reason, penalty)
                selected[related_path] = self._merge_file(selected.get(related_path), expanded)

        expanded_files = list(selected.values())
        expanded_files.sort(key=lambda ranked_file: (-ranked_file.score, ranked_file.path))
        return expanded_files[:limit]

    def _related_paths(self, file: RankedFile) -> list[tuple[str, str, int]]:
        """Return bounded dependency and dependent paths for one ranked file."""
        related = []
        for dependency in file.dependencies[: self.dependency_limit]:
            related.append((dependency, f"dependency-of:{file.path}", 8))
        for dependent in file.dependents[: self.dependency_limit]:
            related.append((dependent, f"dependent-of:{file.path}", 10))
        return related

    def _expanded_file(
        self,
        path: str,
        entry: FileIndexEntry,
        source_file: RankedFile,
        reason: str,
        penalty: int,
    ) -> RankedFile:
        """Create a ranked file for dependency expansion."""
        score = max(source_file.score - penalty, 1)
        return RankedFile(
            path=path,
            score=score,
            reasons=[reason],
            source_metadata={
                "extension": entry.get("extension", ""),
                "size": entry.get("size", 0),
                "truncated": bool(entry.get("truncated", False)),
                "expanded_from": source_file.path,
            },
            dependencies=self.dependency_mapper.get_dependencies(path),
            dependents=self.dependency_mapper.get_dependents(path),
        )

    def _merge_file(self, current: RankedFile | None, new: RankedFile) -> RankedFile:
        """Merge ranked-file evidence for the same path."""
        if current is None:
            return new
        if new.score > current.score:
            score = new.score
        else:
            score = current.score
        source_metadata = dict(current.source_metadata)
        source_metadata.update(new.source_metadata)
        return RankedFile(
            path=current.path,
            score=score,
            reasons=sorted(set(current.reasons + new.reasons)),
            source_metadata=source_metadata,
            dependencies=sorted(set(current.dependencies + new.dependencies)),
            dependents=sorted(set(current.dependents + new.dependents)),
        )

    def _fallback_files(
        self,
        index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
    ) -> list[RankedFile]:
        """Choose stable overview or active files when query terms do not match."""
        preferred_names = {"README.md", "app.py", "main.py", "index.js", "package.json"}
        active = repository_state.changed_files + repository_state.staged_files + repository_state.untracked_files
        candidates = [path for path in active if path in index]
        candidates.extend(
            path for path in sorted(index)
            if os.path.basename(path) in preferred_names and path not in candidates
        )
        candidates.extend(path for path in sorted(index) if path not in candidates)

        ranked = []
        for path in candidates:
            entry = index[path]
            reason = "fallback:active-file" if path in active else "fallback:overview"
            ranked.append(
                RankedFile(
                    path=path,
                    score=1,
                    reasons=[reason],
                    source_metadata={
                        "extension": entry.get("extension", ""),
                        "size": entry.get("size", 0),
                        "truncated": bool(entry.get("truncated", False)),
                    },
                    dependencies=self.dependency_mapper.get_dependencies(path),
                    dependents=self.dependency_mapper.get_dependents(path),
                )
            )
        return ranked

    def _merge_semantic_results(
        self,
        ranked: list[RankedFile],
        query: str,
        index: dict[str, FileIndexEntry],
        repository_state: RepositoryState,
        limit: int,
    ) -> list[RankedFile]:
        """Merge vector search results as an additional ranking signal."""
        project_path = self.indexer.project_path
        if not project_path:
            return ranked
        try:
            response = self.embedding_index_builder.semantic_search(
                project_path=project_path,
                query=query,
                limit=max(limit * 2, 4),
            )
        except Exception as error:
            log.warning("Semantic retrieval skipped: %s", error)
            return ranked

        by_path = {file.path: file for file in ranked}
        semantic_by_path: dict[str, list[SearchResult]] = {}
        for result in response.results:
            if result.chunk.file_path in index:
                semantic_by_path.setdefault(result.chunk.file_path, []).append(result)

        for path, results in semantic_by_path.items():
            semantic_score = max(int(result.similarity_score * 30) for result in results)
            best = max(results, key=lambda item: item.similarity_score)
            reason = (
                f"semantic:{best.similarity_score:.2f}"
                + (f":{best.chunk.symbol_name}" if best.chunk.symbol_name else "")
            )
            entry = index[path]
            semantic_file = RankedFile(
                path=path,
                score=semantic_score,
                reasons=[reason],
                source_metadata={
                    "extension": entry.get("extension", ""),
                    "size": entry.get("size", 0),
                    "truncated": bool(entry.get("truncated", False)),
                    "semantic": {
                        "score": best.similarity_score,
                        "chunk_id": best.chunk.chunk_id,
                        "symbol_name": best.chunk.symbol_name,
                        "backend": response.metadata.get("backend"),
                        "provider": response.metadata.get("provider"),
                    },
                },
                dependencies=self.dependency_mapper.get_dependencies(path),
                dependents=self.dependency_mapper.get_dependents(path),
            )
            current = by_path.get(path)
            by_path[path] = self._merge_file(current, semantic_file) if current else semantic_file

        merged = list(by_path.values())
        merged.sort(key=lambda file: (-file.score, file.path))
        return merged
