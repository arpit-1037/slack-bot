"""Repository-memory quality benchmark suite."""

from __future__ import annotations

from src.memory.memory_models import RepositoryFact, RepositoryMemory
from src.memory.memory_retriever import MemoryRetriever
from tests.evaluation.benchmark_metrics import BenchmarkMetrics
from tests.evaluation.benchmark_models import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkObservation,
    BenchmarkSuite,
)


class StaticMemoryStore:
    """Read-only memory store used by deterministic memory benchmarks."""

    def __init__(self, memory: RepositoryMemory) -> None:
        self.memory = memory

    def load_memory(self) -> RepositoryMemory:
        """Return the fixed benchmark memory fixture."""
        return self.memory


class MemoryEvaluator:
    """Evaluate MemoryRetriever against known repository facts."""

    def __init__(self) -> None:
        memory = RepositoryMemory(
            repo_path="/benchmark/repository",
            repo_id="benchmark-repository",
            facts=[
                RepositoryFact(
                    id="memory-git-module",
                    fact_type="module",
                    key="git module",
                    value="src/tools/git_tool.py",
                    confidence=0.99,
                    file_path="src/tools/git_tool.py",
                    tags=["git", "module", "tool"],
                ),
                RepositoryFact(
                    id="memory-auth-location",
                    fact_type="architecture",
                    key="authentication location",
                    value="src/router/intent_router.py",
                    confidence=0.99,
                    file_path="src/router/intent_router.py",
                    tags=["authentication", "auth", "location"],
                ),
                RepositoryFact(
                    id="memory-slack-entry",
                    fact_type="entry_point",
                    key="Slack event processing start",
                    value="src/slack/slack_handler.py",
                    confidence=0.98,
                    file_path="src/slack/slack_handler.py",
                    tags=["slack", "events", "entry"],
                ),
            ],
        )
        self.retriever = MemoryRetriever(store=StaticMemoryStore(memory))
        self.metrics = BenchmarkMetrics()

    def evaluate(
        self,
        case: BenchmarkCase,
        context: BenchmarkContext,
    ) -> BenchmarkObservation:
        """Retrieve memory and score hit rate, precision, and recall."""
        query = str(case.input_data["query"])
        result = self.retriever.retrieve_memory(
            query,
            project_path=context.project_path,
            min_confidence=float(case.input_data.get("min_confidence") or 0.8),
            max_results=int(case.input_data.get("max_results") or 3),
        )
        retrieved_ids = [entry.fact.id for entry in result.entries]
        relevant_ids = [str(item) for item in case.expected_output["fact_ids"]]
        scores = self.metrics.precision_recall(retrieved_ids, relevant_ids)
        metrics = {
            "memory_hit_rate": float(result.hit),
            "memory_precision": scores["retrieval_precision"],
            "memory_recall": scores["retrieval_recall"],
        }
        passed = result.hit and metrics["memory_recall"] == 1.0
        actual = {
            "hit": result.hit,
            "best_confidence": result.best_confidence,
            "fact_ids": retrieved_ids,
            "paths": [entry.fact.file_path for entry in result.entries],
        }
        return BenchmarkObservation(
            actual_output=actual,
            passed=passed,
            metrics=metrics,
            failure_message=(
                ""
                if passed
                else f"Expected memory facts {relevant_ids!r}, received {actual!r}."
            ),
            failure_category="memory_miss",
            metadata={"query": query},
        )


def create_memory_suite() -> BenchmarkSuite:
    """Return fixture-backed repository-memory cases."""
    evaluator = MemoryEvaluator()
    return BenchmarkSuite(
        name="memory",
        description="Measures repository-memory hit rate, precision, and recall.",
        evaluator=evaluator.evaluate,
        metric_names=("memory_hit_rate", "memory_precision", "memory_recall"),
        cases=[
            BenchmarkCase(
                id="memory-git-module",
                name="Memory locates git module",
                input_data={"query": "Which module handles git?"},
                expected_output={"fact_ids": ["memory-git-module"]},
                category="memory_lookup",
            ),
            BenchmarkCase(
                id="memory-auth-location",
                name="Memory locates authentication",
                input_data={"query": "Where is authentication located?"},
                expected_output={"fact_ids": ["memory-auth-location"]},
                category="memory_lookup",
            ),
            BenchmarkCase(
                id="memory-slack-entry",
                name="Memory locates Slack entry point",
                input_data={"query": "Where does Slack event processing start?"},
                expected_output={"fact_ids": ["memory-slack-entry"]},
                category="memory_lookup",
            ),
        ],
    )
