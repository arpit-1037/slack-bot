"""Deterministic repository retrieval package."""

__all__ = [
    "CodeSnippet",
    "RankedFile",
    "RankedSymbol",
    "RepositoryRetrievalEngine",
    "RetrievalContext",
    "RetrievalResult",
]


def __getattr__(name: str):
    """Lazily expose retrieval classes without creating package import cycles."""
    if name == "RepositoryRetrievalEngine":
        from src.retrieval.retrieval_engine import RepositoryRetrievalEngine

        return RepositoryRetrievalEngine

    if name in {"CodeSnippet", "RankedFile", "RankedSymbol", "RetrievalContext", "RetrievalResult"}:
        from src.retrieval import retrieval_models

        return getattr(retrieval_models, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
