"""Deterministic score fusion for hybrid repository retrieval."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Mapping

from src.hybrid_retrieval.retrieval_models import (
    SIGNAL_DEPENDENCY,
    SIGNAL_GIT,
    SIGNAL_KEYWORD,
    SIGNAL_SEMANTIC,
    RankedCandidate,
    RetrievalScore,
    RetrievalSignal,
)

DEFAULT_SIGNAL_WEIGHTS: dict[str, float] = {
    SIGNAL_SEMANTIC: 0.40,
    SIGNAL_DEPENDENCY: 0.30,
    SIGNAL_KEYWORD: 0.20,
    SIGNAL_GIT: 0.10,
}


def normalize_scores(scores: Mapping[str, float]) -> dict[str, float]:
    """Normalize raw scores to a 0-100 range while preserving deterministic ties."""
    if not scores:
        return {}

    maximum = max(scores.values())
    minimum = min(scores.values())
    if maximum <= 0:
        return {key: 0.0 for key in scores}
    if maximum == minimum:
        return {key: 100.0 if value > 0 else 0.0 for key, value in scores.items()}

    span = maximum - minimum
    return {
        key: round(((value - minimum) / span) * 100, 4) if value > 0 else 0.0
        for key, value in scores.items()
    }


def calculate_final_score(
    signals: list[RetrievalSignal],
    weights: Mapping[str, float] | None = None,
) -> RetrievalScore:
    """Calculate a weighted final score from normalized retrieval signals."""
    active_weights = _normalized_weights(weights or DEFAULT_SIGNAL_WEIGHTS)
    source_scores: dict[str, float] = defaultdict(float)
    for signal in signals:
        source_scores[signal.source] = max(source_scores[signal.source], signal.normalized_score)

    final_score = sum(source_scores.get(source, 0.0) * weight for source, weight in active_weights.items())
    return RetrievalScore(
        keyword_score=round(source_scores.get(SIGNAL_KEYWORD, 0.0), 4),
        dependency_score=round(source_scores.get(SIGNAL_DEPENDENCY, 0.0), 4),
        semantic_score=round(source_scores.get(SIGNAL_SEMANTIC, 0.0), 4),
        git_score=round(source_scores.get(SIGNAL_GIT, 0.0), 4),
        final_score=round(final_score, 4),
        weights=dict(active_weights),
        metadata={"source_scores": dict(sorted(source_scores.items()))},
    )


def fuse_scores(
    candidates: list[RankedCandidate],
    weights: Mapping[str, float] | None = None,
) -> list[RankedCandidate]:
    """Normalize and fuse all candidate signals into final ranking scores."""
    if not candidates:
        return []

    raw_by_source: dict[str, dict[str, float]] = defaultdict(dict)
    for candidate in candidates:
        source_scores: dict[str, float] = defaultdict(float)
        for signal in candidate.signals:
            source_scores[signal.source] = max(source_scores[signal.source], signal.raw_score)
        for source, score in source_scores.items():
            raw_by_source[source][candidate.candidate_id] = score

    normalized_by_source = {
        source: normalize_scores(scores)
        for source, scores in raw_by_source.items()
    }

    fused: list[RankedCandidate] = []
    for candidate in candidates:
        normalized_signals = [
            replace(
                signal,
                normalized_score=normalized_by_source.get(signal.source, {}).get(candidate.candidate_id, 0.0),
            )
            for signal in candidate.signals
        ]
        score = calculate_final_score(normalized_signals, weights=weights)
        score_metadata = dict(candidate.score_metadata)
        score_metadata["hybrid_score"] = score
        score_metadata["raw_scores"] = _raw_score_summary(candidate.signals)
        fused.append(
            replace(
                candidate,
                retrieval_systems=sorted({signal.source for signal in normalized_signals}),
                signals=normalized_signals,
                score=score,
                score_metadata=score_metadata,
            )
        )
    return fused


class ScoreFusion:
    """Small injectable wrapper around score-fusion functions."""

    def __init__(self, weights: Mapping[str, float] | None = None) -> None:
        self.weights = dict(weights or DEFAULT_SIGNAL_WEIGHTS)

    def normalize_scores(self, scores: Mapping[str, float]) -> dict[str, float]:
        """Normalize raw scores to 0-100."""
        return normalize_scores(scores)

    def calculate_final_score(self, signals: list[RetrievalSignal]) -> RetrievalScore:
        """Calculate a weighted final score."""
        return calculate_final_score(signals, weights=self.weights)

    def fuse_scores(self, candidates: list[RankedCandidate]) -> list[RankedCandidate]:
        """Fuse candidate signals using configured weights."""
        return fuse_scores(candidates, weights=self.weights)


def _normalized_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Return non-negative weights normalized to sum to one."""
    clean = {source: max(float(value), 0.0) for source, value in weights.items()}
    total = sum(clean.values())
    if total <= 0:
        return dict(DEFAULT_SIGNAL_WEIGHTS)
    return {source: value / total for source, value in clean.items()}


def _raw_score_summary(signals: list[RetrievalSignal]) -> dict[str, float]:
    """Summarize max raw score per source for ranking explanations."""
    summary: dict[str, float] = defaultdict(float)
    for signal in signals:
        summary[signal.source] = max(summary[signal.source], signal.raw_score)
    return dict(sorted(summary.items()))
