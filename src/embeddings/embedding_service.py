"""Provider-agnostic embedding generation with lazy sentence-transformers support."""

from __future__ import annotations

import hashlib
import importlib
import math
import re
from typing import Iterable

from src.utils.helpers import bool_env, get_logger, int_env

log = get_logger(__name__)


class EmbeddingService:
    """Generate embeddings for text using a lazy provider with deterministic fallback."""

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        fallback_dimension: int | None = None,
        force_fallback: bool | None = None,
    ) -> None:
        self.model_name = model_name
        self.fallback_dimension = fallback_dimension or int_env("EMBEDDING_FALLBACK_DIMENSION", 384, 8)
        self.force_fallback = (
            bool_env("EMBEDDING_FORCE_FALLBACK", False)
            if force_fallback is None
            else force_fallback
        )
        self._model = None
        self._model_load_attempted = False
        self._provider_failed = False
        self._provider_name = "sentence-transformers"
        self._cache: dict[str, list[float]] = {}

    @property
    def provider_name(self) -> str:
        """Return the active provider name."""
        if self.force_fallback:
            return "hash-fallback"
        return self._provider_name

    def generate_embedding(self, text: str) -> list[float]:
        """Generate one embedding vector."""
        return self.generate_embeddings([text])[0]

    def generate_embeddings(self, texts: Iterable[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts with stable caching."""
        text_list = [text or "" for text in texts]
        embeddings: list[list[float] | None] = []
        missing_texts = []
        missing_indices = []

        for index, text in enumerate(text_list):
            cache_key = self._cache_key(text)
            cached = self._cache.get(cache_key)
            if cached is None:
                embeddings.append(None)
                missing_texts.append(text)
                missing_indices.append(index)
            else:
                embeddings.append(cached)

        if missing_texts:
            generated = self._generate_uncached(missing_texts)
            for index, text, vector in zip(missing_indices, missing_texts, generated):
                self._cache[self._cache_key(text)] = vector
                embeddings[index] = vector

        return [vector or self._hash_embedding("") for vector in embeddings]

    def _generate_uncached(self, texts: list[str]) -> list[list[float]]:
        if not self.force_fallback and not self._provider_failed:
            model = self._load_model()
            if model is not None:
                try:
                    vectors = model.encode(texts, normalize_embeddings=True)
                    return [self._vector_to_list(vector) for vector in vectors]
                except Exception:
                    log.exception("Embedding provider failed; using deterministic fallback.")
                    self._provider_failed = True

        self._provider_name = "hash-fallback"
        return [self._hash_embedding(text) for text in texts]

    def _load_model(self):
        if self._model is not None:
            return self._model
        if self._model_load_attempted:
            return None
        self._model_load_attempted = True
        try:
            sentence_transformers = importlib.import_module("sentence_transformers")
            model_class = getattr(sentence_transformers, "SentenceTransformer")
            self._model = model_class(self.model_name)
            self._provider_name = "sentence-transformers"
            log.info("Loaded embedding model name=%s", self.model_name)
            return self._model
        except Exception as error:
            log.info(
                "Could not load sentence-transformers model; using deterministic fallback. "
                "Set EMBEDDING_FORCE_FALLBACK=true to skip provider loading. error=%s",
                error,
            )
            self._provider_name = "hash-fallback"
            return None

    def _hash_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.fallback_dimension
        tokens = self._tokens(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.fallback_dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 8) for value in vector]

    def _tokens(self, text: str) -> list[str]:
        split_text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        return [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", split_text)
            if len(token) > 1
        ]

    def _vector_to_list(self, vector) -> list[float]:
        try:
            return [float(value) for value in vector.tolist()]
        except AttributeError:
            return [float(value) for value in vector]

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model_name}:{text}".encode("utf-8")).hexdigest()
