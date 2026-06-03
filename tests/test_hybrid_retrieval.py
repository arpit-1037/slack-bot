"""Testable examples for hybrid repository retrieval."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.embeddings.embedding_models import CodeChunk, EmbeddingSearchResponse, SearchResult
from src.hybrid_retrieval.hybrid_retriever import HybridRetriever, calculate_git_relevance
from src.hybrid_retrieval.retrieval_models import (
    SIGNAL_KEYWORD,
    SIGNAL_SEMANTIC,
    RankedCandidate,
    RetrievalSignal,
)
from src.hybrid_retrieval.score_fusion import fuse_scores
from src.repository.repository_indexer import RepositoryIndexer
from src.repository.repository_state import RepositoryState


class FakeSemanticBuilder:
    """Semantic builder that returns a deterministic hand-picked code chunk."""

    def semantic_search(
        self,
        project_path: str,
        query: str,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> EmbeddingSearchResponse:
        del project_path, limit, request_id
        return EmbeddingSearchResponse(
            query=query,
            results=[
                SearchResult(
                    chunk=CodeChunk(
                        chunk_id="access-url-chunk",
                        file_path="src/access_urls.py",
                        content="def issue_one_time_url(user): return user",
                        chunk_type="function",
                        symbol_name="issue_one_time_url",
                        line_start=1,
                        line_end=2,
                    ),
                    similarity_score=0.94,
                )
            ],
            model_name="fake",
            collection_name="fake",
            metadata={"provider": "fake", "backend": "memory"},
        )


class HybridScoreFusionTest(unittest.TestCase):
    """Examples for weighted score fusion."""

    def test_semantic_signal_can_outrank_keyword_only_candidate(self) -> None:
        keyword = RankedCandidate(
            candidate_id="file:src/auth.py:",
            candidate_type="file",
            file_path="src/auth.py",
            signals=[
                RetrievalSignal(
                    source=SIGNAL_KEYWORD,
                    raw_score=20,
                    reason="keyword-match",
                    file_path="src/auth.py",
                )
            ],
        )
        semantic = RankedCandidate(
            candidate_id="file:src/access_urls.py:",
            candidate_type="file",
            file_path="src/access_urls.py",
            signals=[
                RetrievalSignal(
                    source=SIGNAL_SEMANTIC,
                    raw_score=0.94,
                    reason="semantic:0.94",
                    file_path="src/access_urls.py",
                )
            ],
        )

        fused = sorted(fuse_scores([keyword, semantic]), key=lambda item: -item.score.final_score)

        self.assertEqual(fused[0].file_path, "src/access_urls.py")
        self.assertGreater(fused[0].score.semantic_score, 0)
        self.assertGreater(fused[1].score.keyword_score, 0)


class HybridRetrieverTest(unittest.TestCase):
    """Examples for semantic, dependency, and git-aware hybrid retrieval."""

    def test_semantic_result_is_merged_without_exact_keyword_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_semantic_repository(Path(tmp))
            retriever = HybridRetriever(
                indexer=RepositoryIndexer(),
                embedding_index_builder=FakeSemanticBuilder(),
                enable_semantic_search=True,
                max_files=3,
            )

            result = retriever.retrieve(str(root), "temporary authentication links")
            paths = [file.path for file in result.files]

            self.assertIn("src/access_urls.py", paths)
            self.assertTrue(any("semantic:" in ",".join(file.reasons) for file in result.files))
            self.assertIn("src/access_urls.py", result.formatted_context)

    def test_dependency_signal_adds_related_imported_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_dependency_repository(Path(tmp))
            retriever = HybridRetriever(max_files=4, dependency_limit=2)

            result = retriever.retrieve(str(root), "authenticate credentials")
            paths = [file.path for file in result.files]
            jwt_reasons = ",".join(
                reason
                for file in result.files
                if file.path == "src/jwt_service.py"
                for reason in file.reasons
            )

            self.assertIn("src/auth.py", paths)
            self.assertIn("src/jwt_service.py", paths)
            self.assertIn("dependency-of:src/auth.py", jwt_reasons)

    def test_git_relevance_scores_working_tree_and_recent_commit_files(self) -> None:
        state = RepositoryState(
            repo_path="/repo",
            changed_files=["src/auth.py"],
            staged_files=["src/routes.py"],
            untracked_files=["src/new_feature.py"],
        )

        self.assertEqual(calculate_git_relevance("src/routes.py", state), 90)
        self.assertEqual(calculate_git_relevance("src/auth.py", state), 85)
        self.assertEqual(calculate_git_relevance("src/new_feature.py", state), 70)
        self.assertGreater(calculate_git_relevance("src/old.py", state, ["src/old.py"]), 0)

    def _write_semantic_repository(self, root: Path) -> Path:
        source = root / "src"
        source.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "access_urls.py").write_text(
            "def issue_one_time_url(user):\n"
            "    \"\"\"Issue a one-time access URL for a user.\"\"\"\n"
            "    return f'/access/{user}'\n",
            encoding="utf-8",
        )
        (source / "audit_log.py").write_text(
            "def write_audit_log(event):\n"
            "    return event\n",
            encoding="utf-8",
        )
        return root

    def _write_dependency_repository(self, root: Path) -> Path:
        source = root / "src"
        source.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "jwt_service.py").write_text(
            "class JWTService:\n"
            "    def create_token(self, username):\n"
            "        return f'jwt:{username}'\n",
            encoding="utf-8",
        )
        (source / "auth.py").write_text(
            "from src.jwt_service import JWTService\n"
            "\n"
            "\n"
            "def authenticate_user(username, password):\n"
            "    \"\"\"Authenticate credentials.\"\"\"\n"
            "    if not password:\n"
            "        return None\n"
            "    return JWTService().create_token(username)\n",
            encoding="utf-8",
        )
        return root


if __name__ == "__main__":
    unittest.main()
