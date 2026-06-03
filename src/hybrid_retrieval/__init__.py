"""Hybrid repository retrieval package."""

__all__ = [
    "ContextOptimizer",
    "HybridRetriever",
    "HybridRetrievalResult",
    "OptimizedContext",
    "RankedCandidate",
    "RetrievalRanker",
    "RetrievalScore",
    "RetrievalSignal",
    "ScoreFusion",
    "calculate_git_relevance",
    "fuse_scores",
]


def __getattr__(name: str):
    """Lazily expose hybrid retrieval classes without import cycles."""
    if name == "ContextOptimizer":
        from src.hybrid_retrieval.context_optimizer import ContextOptimizer

        return ContextOptimizer
    if name in {"HybridRetriever", "calculate_git_relevance"}:
        from src.hybrid_retrieval.hybrid_retriever import HybridRetriever, calculate_git_relevance

        return {"HybridRetriever": HybridRetriever, "calculate_git_relevance": calculate_git_relevance}[name]
    if name in {"HybridRetrievalResult", "OptimizedContext", "RankedCandidate", "RetrievalScore", "RetrievalSignal"}:
        from src.hybrid_retrieval import retrieval_models

        return getattr(retrieval_models, name)
    if name == "RetrievalRanker":
        from src.hybrid_retrieval.retrieval_ranker import RetrievalRanker

        return RetrievalRanker
    if name in {"ScoreFusion", "fuse_scores"}:
        from src.hybrid_retrieval.score_fusion import ScoreFusion, fuse_scores

        return {"ScoreFusion": ScoreFusion, "fuse_scores": fuse_scores}[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
