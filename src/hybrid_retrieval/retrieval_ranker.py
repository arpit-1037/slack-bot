"""Ranking helpers for hybrid retrieval candidates."""

from __future__ import annotations

from typing import Mapping

from src.hybrid_retrieval.retrieval_models import RankedCandidate
from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry
from src.retrieval.retrieval_models import RankedFile, RankedSymbol


class RetrievalRanker:
    """Rank hybrid candidates and convert them to existing retrieval models."""

    def rank_candidates(
        self,
        candidates: list[RankedCandidate],
        limit: int | None = None,
    ) -> list[RankedCandidate]:
        """Return candidates sorted by final score and stable repository location."""
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                -candidate.score.final_score,
                candidate.file_path,
                candidate.symbol_name or "",
                candidate.line_start or 0,
                candidate.candidate_id,
            ),
        )
        return ranked[:limit] if limit is not None else ranked

    def rank_files(
        self,
        candidates: list[RankedCandidate],
        repository_index: Mapping[str, FileIndexEntry],
        dependency_mapper: DependencyMapper | None = None,
        limit: int | None = None,
    ) -> list[RankedFile]:
        """Return ranked file results from hybrid-ranked candidates."""
        best_by_path: dict[str, RankedCandidate] = {}
        for candidate in self.rank_candidates(candidates):
            if candidate.file_path not in repository_index:
                continue
            current = best_by_path.get(candidate.file_path)
            if current is None or candidate.score.final_score > current.score.final_score:
                best_by_path[candidate.file_path] = candidate

        ranked_files = [
            self._ranked_file(candidate, repository_index[candidate.file_path], dependency_mapper)
            for candidate in best_by_path.values()
        ]
        ranked_files.sort(key=lambda file: (-file.score, file.path))
        return ranked_files[:limit] if limit is not None else ranked_files

    def rank_symbols(
        self,
        symbols: list[RankedSymbol],
        limit: int | None = None,
    ) -> list[RankedSymbol]:
        """Return symbols sorted by score and stable location."""
        ranked = sorted(symbols, key=lambda symbol: (-symbol.score, symbol.file_path, symbol.line_start, symbol.name))
        return ranked[:limit] if limit is not None else ranked

    def explain_ranking(self, candidate: RankedCandidate) -> str:
        """Return a compact explanation for one ranked candidate."""
        score = candidate.score
        parts = [
            f"{candidate.file_path} score {score.final_score:.2f}",
            f"semantic {score.semantic_score:.2f}",
            f"dependency {score.dependency_score:.2f}",
            f"keyword {score.keyword_score:.2f}",
            f"git {score.git_score:.2f}",
        ]
        reasons = ", ".join(sorted({signal.reason for signal in candidate.signals})[:5])
        if reasons:
            parts.append(f"reasons: {reasons}")
        return "; ".join(parts)

    def _ranked_file(
        self,
        candidate: RankedCandidate,
        entry: FileIndexEntry,
        dependency_mapper: DependencyMapper | None,
    ) -> RankedFile:
        """Convert one hybrid candidate into the existing ranked-file model."""
        reasons = sorted({signal.reason for signal in candidate.signals})
        metadata = {
            "extension": entry.get("extension", ""),
            "size": entry.get("size", 0),
            "truncated": bool(entry.get("truncated", False)),
            "hybrid": candidate.score_metadata,
            "retrieval_systems": list(candidate.retrieval_systems),
        }
        return RankedFile(
            path=candidate.file_path,
            score=max(1, int(round(candidate.score.final_score))),
            reasons=reasons,
            source_metadata=metadata,
            dependencies=dependency_mapper.get_dependencies(candidate.file_path) if dependency_mapper else [],
            dependents=dependency_mapper.get_dependents(candidate.file_path) if dependency_mapper else [],
        )


def rank_candidates(candidates: list[RankedCandidate], limit: int | None = None) -> list[RankedCandidate]:
    """Return hybrid candidates sorted by final score."""
    return RetrievalRanker().rank_candidates(candidates, limit=limit)


def rank_files(
    candidates: list[RankedCandidate],
    repository_index: Mapping[str, FileIndexEntry],
    dependency_mapper: DependencyMapper | None = None,
    limit: int | None = None,
) -> list[RankedFile]:
    """Return ranked files from hybrid candidates."""
    return RetrievalRanker().rank_files(candidates, repository_index, dependency_mapper, limit=limit)


def rank_symbols(symbols: list[RankedSymbol], limit: int | None = None) -> list[RankedSymbol]:
    """Return ranked symbols sorted by score."""
    return RetrievalRanker().rank_symbols(symbols, limit=limit)
