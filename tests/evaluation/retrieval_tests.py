"""Repository, hybrid, and semantic retrieval benchmark suite."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from src.embeddings.embedding_service import EmbeddingService
from src.embeddings.index_builder import EmbeddingIndexBuilder
from src.embeddings.vector_store import VectorStore
from src.hybrid_retrieval.hybrid_retriever import HybridRetriever
from src.repository.repository_indexer import RepositoryIndexer
from src.repository.state_cache import RepositoryStateCache
from src.repository.state_refresher import RepositoryStateRefresher
from src.retrieval.retrieval_engine import RepositoryRetrievalEngine
from tests.evaluation.benchmark_metrics import BenchmarkMetrics
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class RetrievalEvaluator:
    """Evaluate ranked repository paths from all retrieval modes."""

    def __init__(self) -> None:
        self.metrics = BenchmarkMetrics()
        self._retrievers: dict[tuple[str, str], object] = {}

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Retrieve ranked files and compare them with relevant paths."""
        query = str(case.input_data["query"])
        mode = str(case.input_data.get("mode") or "repository")
        top_k = int(case.input_data.get("top_k") or 5)
        result = self._retrieve(mode, context.project_path, query, context.run_id)
        paths = [file.path for file in result.files]
        relevant = [str(path) for path in case.expected_output["relevant_paths"]]
        scores = self.metrics.precision_recall(paths, relevant)
        scores["top_k_accuracy"] = self.metrics.top_k_accuracy(paths, relevant, top_k)
        passed = scores["retrieval_recall"] == 1.0 and scores["top_k_accuracy"] == 1.0
        actual = {
            "mode": mode,
            "ranked_paths": paths,
            "top_k": top_k,
            "matched_paths": sorted(set(paths) & set(relevant)),
        }
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics=scores,
            failure_message=(
                ""
                if passed
                else f"Relevant paths {relevant!r} were not retrieved in top {top_k}: {paths!r}."
            ),
            failure_category="retrieval_miss",
            metadata={"query": query, "mode": mode},
        )

    def _retrieve(
        self,
        mode: str,
        project_path: str,
        query: str,
        request_id: str,
    ):
        key = (mode, project_path)
        retriever = self._retrievers.get(key)
        if retriever is None:
            retriever = self._create_retriever(mode, project_path)
            self._retrievers[key] = retriever
        if mode == "repository":
            return retriever.retrieve_context(
                project_path,
                query,
                request_id=request_id,
                max_files=6,
                max_symbols=10,
            )
        return retriever.retrieve(
            project_path,
            query,
            request_id=request_id,
            max_files=6,
            max_symbols=10,
        )

    def _create_retriever(self, mode: str, project_path: str) -> object:
        indexer = self._benchmark_indexer(project_path, mode)
        if mode == "repository":
            return RepositoryRetrievalEngine(
                indexer=indexer,
                enable_repository_memory=False,
                enable_semantic_search=False,
            )
        if mode == "hybrid":
            return HybridRetriever(
                indexer=indexer,
                enable_semantic_search=False,
            )
        if mode == "semantic":
            embedding_builder = EmbeddingIndexBuilder(
                indexer=indexer,
                embedding_service=EmbeddingService(force_fallback=True),
                vector_store=VectorStore(force_memory=True),
            )
            return HybridRetriever(
                indexer=indexer,
                enable_semantic_search=True,
                embedding_index_builder=embedding_builder,
            )
        raise ValueError(f"Unknown retrieval benchmark mode: {mode}")

    def _benchmark_indexer(self, project_path: str, mode: str) -> RepositoryIndexer:
        """Create an indexer whose state cache lives in the system temp directory."""
        digest = hashlib.sha1(f"{project_path}:{mode}".encode("utf-8")).hexdigest()[:12]
        cache_path = (
            Path(tempfile.gettempdir())
            / "slack-claude-bot-benchmarks"
            / digest
            / "state.json"
        )
        cache = RepositoryStateCache(project_path, cache_path=cache_path)
        refresher = RepositoryStateRefresher(
            repo_path=project_path,
            cache=cache,
        )
        return RepositoryIndexer(state_refresher=refresher)


def create_retrieval_suite() -> BenchmarkSuite:
    """Return repository, hybrid, and semantic retrieval cases."""
    evaluator = RetrievalEvaluator()
    return BenchmarkSuite(
        name="retrieval",
        description="Measures retrieval precision, recall, and top-k accuracy.",
        evaluator=evaluator.evaluate,
        metric_names=("retrieval_precision", "retrieval_recall", "top_k_accuracy"),
        cases=[
            BenchmarkCase(
                id="retrieval-slack-entry",
                name="Repository search locates Slack event handler",
                input_data={
                    "query": "Where does Slack event processing start?",
                    "mode": "repository",
                    "top_k": 5,
                },
                expected_output={"relevant_paths": ["src/slack/slack_handler.py"]},
                category="repository_search",
            ),
            BenchmarkCase(
                id="retrieval-followup-resolver",
                name="Hybrid retrieval locates follow-up resolver",
                input_data={
                    "query": "Which module resolves short conversation follow-ups?",
                    "mode": "hybrid",
                    "top_k": 5,
                },
                expected_output={
                    "relevant_paths": ["src/query_understanding/followup_resolver.py"]
                },
                category="hybrid_retrieval",
            ),
            BenchmarkCase(
                id="retrieval-embedding-service",
                name="Semantic retrieval locates embedding generation",
                input_data={
                    "query": "Where are repository embeddings generated?",
                    "mode": "semantic",
                    "top_k": 5,
                },
                expected_output={"relevant_paths": ["src/embeddings/embedding_service.py"]},
                category="semantic_retrieval",
            ),
        ],
    )
