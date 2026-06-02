"""Testable examples for embeddings and vector search workflows."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.embeddings.chunker import CodeChunker
from src.embeddings.embedding_models import EmbeddingRecord
from src.embeddings.embedding_service import EmbeddingService
from src.embeddings.index_builder import EmbeddingIndexBuilder
from src.embeddings.vector_store import VectorStore
from src.repository.repository_indexer import RepositoryIndexer
from src.retrieval.retrieval_engine import RepositoryRetrievalEngine


class CodeChunkerTest(unittest.TestCase):
    """Examples for function, class, method, and documentation chunks."""

    def test_chunks_python_symbols_without_embedding_entire_file(self) -> None:
        entry = {
            "extension": ".py",
            "content": (
                "class TokenService:\n"
                "    \"\"\"Issue login tokens.\"\"\"\n"
                "\n"
                "    def create_magic_link(self, user):\n"
                "        return f'/login/{user}'\n"
                "\n"
                "def send_reminder(user):\n"
                "    return user.email\n"
            ),
            "truncated": False,
        }

        chunks = CodeChunker().chunk_file("src/auth.py", entry)
        symbols = {chunk.symbol_name for chunk in chunks}

        self.assertIn("TokenService", symbols)
        self.assertIn("TokenService.create_magic_link", symbols)
        self.assertIn("send_reminder", symbols)
        self.assertTrue(all(chunk.content for chunk in chunks))


class EmbeddingServiceTest(unittest.TestCase):
    """Examples for deterministic offline embedding generation."""

    def test_fallback_embeddings_are_cached_and_normalized(self) -> None:
        service = EmbeddingService(force_fallback=True, fallback_dimension=16)

        first = service.generate_embedding("create magic login link")
        second = service.generate_embedding("create magic login link")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertEqual(service.provider_name, "hash-fallback")

    def test_provider_load_failure_is_cached(self) -> None:
        service = EmbeddingService(fallback_dimension=16)

        with patch(
            "src.embeddings.embedding_service.importlib.import_module",
            side_effect=ImportError("missing optional provider"),
        ) as import_module:
            service.generate_embedding("create magic login link")
            service.generate_embedding("send reminder email")

        self.assertEqual(import_module.call_count, 1)
        self.assertEqual(service.provider_name, "hash-fallback")

    def test_force_fallback_env_skips_provider_load(self) -> None:
        with patch.dict(os.environ, {"EMBEDDING_FORCE_FALLBACK": "true"}):
            service = EmbeddingService(fallback_dimension=16)

            with patch("src.embeddings.embedding_service.importlib.import_module") as import_module:
                vector = service.generate_embedding("offline semantic retrieval")

        self.assertEqual(import_module.call_count, 0)
        self.assertEqual(len(vector), 16)
        self.assertEqual(service.provider_name, "hash-fallback")


class VectorStoreTest(unittest.TestCase):
    """Examples for in-memory vector search."""

    def test_search_returns_ranked_similar_chunks(self) -> None:
        service = EmbeddingService(force_fallback=True, fallback_dimension=32)
        chunker = CodeChunker()
        chunks = chunker.chunk_file(
            "src/auth.py",
            {
                "extension": ".py",
                "content": (
                    "def create_magic_link(user):\n"
                    "    \"\"\"Create a temporary login link.\"\"\"\n"
                    "    return user\n"
                ),
                "truncated": False,
            },
        )
        records = [
            EmbeddingRecord(
                chunk=chunk,
                embedding=service.generate_embedding(chunk.text_for_embedding),
                model_name=service.model_name,
                provider=service.provider_name,
            )
            for chunk in chunks
        ]
        store = VectorStore(force_memory=True)
        store.add_embeddings(records)

        results = store.search(service.generate_embedding("temporary login link"), limit=1)

        self.assertEqual(results[0].chunk.file_path, "src/auth.py")
        self.assertGreaterEqual(results[0].similarity_score, 0)


class EmbeddingIndexBuilderTest(unittest.TestCase):
    """Examples for repository indexing and semantic search."""

    def test_build_index_and_semantic_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_sample_repository(Path(tmp))
            service = EmbeddingService(force_fallback=True, fallback_dimension=32)
            builder = EmbeddingIndexBuilder(
                embedding_service=service,
                vector_store=VectorStore(force_memory=True),
            )

            metadata = builder.build_index(str(root))
            response = builder.semantic_search(str(root), "magic login link", limit=3)

            self.assertGreater(metadata.chunk_count, 0)
            self.assertTrue(response.results)
            self.assertIn("src/auth_links.py", {result.chunk.file_path for result in response.results})

    def test_retrieval_engine_merges_semantic_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_sample_repository(Path(tmp))
            indexer = RepositoryIndexer()
            service = EmbeddingService(force_fallback=True, fallback_dimension=32)
            builder = EmbeddingIndexBuilder(
                indexer=indexer,
                embedding_service=service,
                vector_store=VectorStore(force_memory=True),
            )
            engine = RepositoryRetrievalEngine(
                indexer=indexer,
                embedding_index_builder=builder,
                enable_semantic_search=True,
                max_files=3,
            )

            result = engine.retrieve_context(str(root), "magic login link")

            self.assertIn("src/auth_links.py", [file.path for file in result.files])
            self.assertTrue(
                any("semantic:" in ",".join(file.reasons) for file in result.files)
            )

    def _write_sample_repository(self, root: Path) -> Path:
        source = root / "src"
        source.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "auth_links.py").write_text(
            "def create_magic_link(user):\n"
            "    \"\"\"Create a temporary login link for one-time access.\"\"\"\n"
            "    return f'/login/{user}'\n",
            encoding="utf-8",
        )
        (source / "notifications.py").write_text(
            "def send_reminder(user):\n"
            "    \"\"\"Send a reminder notification.\"\"\"\n"
            "    return user.email\n",
            encoding="utf-8",
        )
        return root


if __name__ == "__main__":
    unittest.main()
